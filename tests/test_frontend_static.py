"""前端静态检查（模板 + static/ 下的样式和脚本）：每个脚本按普通 <script> 语法对、按加载顺序拼起来也对；
按页面顺序一个一个跑（node 的 vm + 假 DOM）加载和启动都不出错；没有真号码；没有被关掉的分支；
脚本里用到的元素 id 页面上都有；页面引用的 /static/ 文件都取得到；演示页能生成成单文件；公开版同步清单有这些文件。
不起服务、不开浏览器。"""

import importlib.util
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

import app as panel

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "static" / "js"
LOCAL = {"Host": "localhost:5001"}


def _load(name, path):
    """scripts/ 下的脚本按文件路径加载（scripts 不是包）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frontend_files():
    return sorted((ROOT / "templates").rglob("*.html")) + sorted((ROOT / "static").rglob("*.js")) + \
        sorted((ROOT / "static").rglob("*.css"))


class _Page(HTMLParser):
    """渲染出来的页面：所有 id、导航的 data-tab、/static/ 引用、脚本加载顺序。"""

    def __init__(self, html):
        super().__init__()
        self.html, self.ids, self.tabs, self.static, self.scripts, self.script_attrs = html, [], [], [], [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.append(a["id"])
        if a.get("data-tab"):
            self.tabs.append(a["data-tab"])
        for k in ("src", "href"):
            if (a.get(k) or "").startswith("/static/"):
                self.static.append(a[k])
        if tag == "script" and (a.get("src") or "").startswith("/static/js/"):
            self.scripts.append(a["src"].split("?")[0].rsplit("/", 1)[1])
            self.script_attrs.append(sorted(a))


def _page():
    r = panel.app.test_client().get("/", headers=LOCAL)
    assert r.status_code == 200
    return _Page(r.get_data(as_text=True))


def _node():
    return shutil.which("node") or next((p for p in ("/opt/homebrew/bin/node", "/usr/local/bin/node") if Path(p).exists()), None)


# ── 脚本：语法、加载顺序 ──────────────────────────────────────

def test_scripts_loaded_once_core_first():
    """static/js 下每个脚本页面都加载、只加载一次；core.js 最先（别的文件一加载就要用它）。
    都是普通 <script>（不带 async / defer / type=module）：core.js 末尾的启动等 DOMContentLoaded，靠的是它触发时所有脚本都执行完了。"""
    p = _page()
    assert sorted(p.scripts) == sorted(f.name for f in JS_DIR.glob("*.js")) and len(set(p.scripts)) == len(p.scripts)
    assert p.scripts[0] == "core.js"
    assert all(attrs == ["src"] for attrs in p.script_attrs), p.script_attrs


# 按普通 <script> 解析（vm.Script）：node --check 会把带 import / export、顶层 await 的文件当模块，把顶层 return 当函数体，都放过去
CLASSIC = "const vm = require('vm'), fs = require('fs'); for (const f of process.argv.slice(1)) new vm.Script(fs.readFileSync(f, 'utf8'), {filename: f});"


def test_js_syntax_each_file_and_all_together(tmp_path):
    """每个文件单独过 node --check（方案的验收写的），再按普通 <script> 解析一遍：页面上遇到 export、顶层 await 这些，整个文件不执行。
    再按加载顺序拼起来解析一次：几个普通 <script> 共用全局，跨文件重名的 const / let（或 const 和 function 重名）
    在浏览器里会让后加载的那个文件整个不执行，拼起来就能查出来。"""
    node = _node()
    if not node:
        pytest.skip("没装 node")
    files = _page().scripts
    for f in files:
        r = subprocess.run([node, "--check", str(JS_DIR / f)], capture_output=True, text=True)
        assert r.returncode == 0, f + "\n" + r.stderr
    both = tmp_path / "all.js"
    both.write_text("\n".join((JS_DIR / f).read_text(encoding="utf-8") for f in files), encoding="utf-8")
    r = subprocess.run([node, "-e", CLASSIC, *[str(JS_DIR / f) for f in files], str(both)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    names = [m.group(1) for f in files
             for m in re.finditer(r"^(?:async\s+)?function\s+([\w$]+)", (JS_DIR / f).read_text(encoding="utf-8"), re.M)]
    assert sorted(n for n in set(names) if names.count(n) > 1) == []   # 顶层函数重名：后加载的会悄悄盖掉前面的


# ── 按页面顺序一个一个跑：加载、启动、事件 ─────────────────────────────

# 在 node 的 vm 里按页面顺序，把每个文件当独立的普通 <script> 跑（共用一个全局，和浏览器一样），配一个宽松的假 DOM；
# fetch 永远不回，只看同步的部分。输出 JSON：{情形: {load, boot, boot_log, fetched, fired}}；
# 情形 all = 全部取到；-xx.js = 这一个没取到（隧道 502、超时），其余照常。
PAGE_SIM = r"""
const vm = require('vm'), fs = require('fs'), path = require('path');
const [dir, ...files] = process.argv.slice(2);
const code = Object.fromEntries(files.map(f => [f, fs.readFileSync(path.join(dir, f), 'utf8')]));
const msg = e => `${e && e.name}: ${e && e.message}`;
const late = [];                                   // 异步里没人接的错误
process.on('unhandledRejection', e => late.push(e));

function page() {
  const reg = [], ready = [], fetched = [], logged = [], els = {};
  const el = name => {
    const v = {innerHTML: '', textContent: '', value: '', className: '', title: '', href: '', disabled: false, checked: false,
               scrollTop: 0, scrollHeight: 0, clientHeight: 0, style: {}, options: [{outerHTML: '<option></option>'}], children: [], files: [],
               dataset: new Proxy({}, {get: (o, k) => k in o ? o[k] : (k === 'tab' ? 'new' : undefined)})};
    return new Proxy(function () {}, {
      get(t, k) {
        if (k === Symbol.toPrimitive) return () => '';
        if (k === 'then') return undefined;                                       // 不是 Promise
        if (k === 'addEventListener') return (type, fn) => reg.push([`${name} ${type}`, fn]);
        if (k === 'classList') return {add() {}, remove() {}, toggle() {}, contains: () => false};
        if (k === 'parentElement' || k === 'firstChild') return el(`${name}.${k}`);
        if (k === 'querySelectorAll') return () => [];
        if (k === 'querySelector' || k === 'closest') return () => null;
        if (k === 'getBoundingClientRect') return () => ({top: 0, left: 0, width: 100, height: 100});
        if (k in v) return v[k];
        if (typeof k === 'string' && k.startsWith('on')) return null;
        return () => {};                                                          // 其余当方法：appendChild、focus、remove……
      },
      set(t, k, val) {
        if (typeof k === 'string' && k.startsWith('on') && typeof val === 'function') reg.push([`${name}.${k}`, val]);
        v[k] = val;
        return true;
      },
    });
  };
  const doc = new Proxy({title: '求职投递面板', hidden: false, activeElement: null}, {
    get(o, k) {
      if (k === 'querySelector') return s => els[s] || (els[s] = el(s));
      if (k === 'getElementById') return s => els['#' + s] || (els['#' + s] = el('#' + s));
      if (k === 'querySelectorAll') return s => [el(s + '[0]'), el(s + '[1]')];
      if (k === 'addEventListener') return (type, fn) => (type === 'DOMContentLoaded' ? ready : reg).push([`document ${type}`, fn]);
      if (k === 'createElement') return t => el(`<${t}>`);
      if (k === 'body') return els.body || (els.body = el('body'));
      return k in o ? o[k] : () => {};                                           // execCommand 等
    },
    set: (o, k, val) => (o[k] = val, true),
  });
  const timer = kind => (fn, ms) => (reg.push([`${kind}(${ms})`, fn]), reg.length);
  const storage = () => { const m = new Map(); return {getItem: k => m.has(k) ? m.get(k) : null, setItem: (k, x) => m.set(k, String(x)), removeItem: k => m.delete(k)}; };
  const g = {
    document: doc, fetch: url => (fetched.push(String(url)), new Promise(() => {})),
    console: {log() {}, info() {}, warn() {}, debug() {}, error: (...a) => logged.push(a.map(String).join(' '))},
    setTimeout: timer('setTimeout'), setInterval: timer('setInterval'), requestAnimationFrame: timer('requestAnimationFrame'),
    clearTimeout() {}, clearInterval() {}, cancelAnimationFrame() {},
    navigator: {clipboard: {writeText: async () => {}}, userAgent: ''},
    location: {href: 'http://localhost/', origin: 'http://localhost', pathname: '/', search: '', hash: '', reload() {}},
    history: {replaceState() {}, pushState() {}}, localStorage: storage(), sessionStorage: storage(),
    URL, URLSearchParams, Blob, TextEncoder, TextDecoder, AbortController,
    FormData: class { append() {} }, CSS: {escape: s => String(s)},
    alert() {}, confirm: () => false, prompt: () => null, open: () => null, scrollTo() {}, scrollY: 0, innerWidth: 1280, innerHeight: 800,
    getComputedStyle: () => ({getPropertyValue: () => ''}), matchMedia: () => ({matches: false, addEventListener() {}}),
  };
  g.window = g;
  g.addEventListener = (type, fn) => reg.push([`window ${type}`, fn]);
  vm.createContext(g);
  const run = f => { try { new vm.Script(code[f], {filename: f}).runInContext(g); } catch (e) { return `${f}: ${msg(e)}`; } };
  return {g, reg, ready, fetched, logged, run};
}

async function scenario(skip) {
  const p = page();
  const out = {load: files.filter(f => f !== skip).map(p.run).filter(Boolean), boot: [], fired: []};
  const n = p.logged.length;
  for (const [what, fn] of p.ready) { try { fn({type: 'DOMContentLoaded'}); } catch (e) { out.boot.push(`${what}: ${msg(e)}`); } }
  out.boot_log = p.logged.slice(n);                // 启动里每句的 try 接住的错误记在 console.error
  out.fetched = p.fetched.slice();
  if (skip) return out;
  // 全部取到、启动完：登记过的事件、定时器各触发一次，不能出「xx is not defined」
  const ev = new Proxy({}, {get: (o, k) => k === 'target' || k === 'currentTarget' ? p.g.document.body : k === 'key' ? 'x'
    : k === 'preventDefault' || k === 'stopPropagation' ? () => {} : undefined});
  const note = what => e => { if (e && e.name === 'ReferenceError') out.fired.push(`${what}: ${e.message}`); };
  late.length = 0;
  for (const [what, fn] of p.reg.slice()) {
    try { const r = fn(ev); if (r && typeof r.then === 'function') r.then(null, note(what)); } catch (e) { note(what)(e); }
  }
  await new Promise(r => setTimeout(r, 50));
  late.forEach(note('（异步）'));
  return out;
}

(async () => {
  const res = {all: await scenario(null)};
  for (const f of files.slice(1)) res['-' + f] = await scenario(f);   // core.js 没取到整页都用不了，不单看
  console.log(JSON.stringify(res));
})();
"""


@pytest.fixture(scope="module")
def page_run(tmp_path_factory):
    node = _node()
    if not node:
        pytest.skip("没装 node")
    sim = tmp_path_factory.mktemp("page_sim") / "page_sim.js"
    sim.write_text(PAGE_SIM, encoding="utf-8")
    r = subprocess.run([node, str(sim), str(JS_DIR), *_page().scripts], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _paths(urls):
    return {u.split("?")[0] for u in urls}


def test_scripts_run_one_by_one_like_the_page(page_run):
    """按页面顺序一个一个跑：加载时不出错（一加载就执行的语句用到后面文件里的函数，浏览器里这个文件后面的部分全不执行，拼成一个文件查不出来）；
    所有脚本执行完才启动（core.js 末尾，DOMContentLoaded），启动不出错，配置、投递队列、网申待办、助手都去拉了；
    之后把登记的事件、定时器各触发一次，没有「xx is not defined」（拆文件时丢了函数、写错名字）。
    触发时报的要是浏览器自带的接口，说明假 DOM 缺它：加进上面 PAGE_SIM 的 g。"""
    r = page_run["all"]
    assert r["load"] == [] and r["boot"] == [] and r["boot_log"] == [], r
    assert {"/api/config", "/api/queue", "/api/wstasks", "/api/agent"} <= _paths(r["fetched"]), r["fetched"]
    assert r["fired"] == [], r["fired"]


def test_one_script_missing_rest_still_starts(page_run):
    """某一个页面脚本没取到（隧道 502、超时、面板正好在重启）：启动里每句单独 try，只少它那一块——
    配置照样读（左下角 Gmail 和简历状态、批次下拉框），投递页的脚本在的话队列照样拉。"""
    assert len(page_run) == len(_page().scripts)   # all + 除 core.js 外每个文件各缺一次
    for key, r in page_run.items():
        if key == "all":
            continue
        assert r["boot"] == [], (key, r["boot"])                  # 没有漏出 try、把后面的启动语句一起带走的错误
        assert "/api/config" in _paths(r["fetched"]), (key, r["fetched"])
        if key != "-mail.js":
            assert "/api/queue" in _paths(r["fetched"]), (key, r["fetched"])


# ── 隐私、被关掉的分支 ─────────────────────────────────────────

def test_no_real_numbers_in_frontend():
    """前端文件里没有像真号码的（number_problems），也没有个人信息关键词：和公开版推送前的扫描同一套规则，新文件没进它的清单也照样查。"""
    sp = ROOT / "scripts" / "sync_public.py"
    if not sp.exists():
        pytest.skip("公开版没有 sync_public.py")
    bad = _load("sync_public_for_test", sp).scan(ROOT, [str(f.relative_to(ROOT)) for f in _frontend_files()])
    assert not bad, bad


SWITCHED_OFF = [   # （正则, 说明, 前面紧挨着比较符时不算：x === false && … 是正常的比较）
    (re.compile(r"\bfalse\s*&&"), "false && …", True),
    (re.compile(r"&&\s*false\b(?!\s*[=!]==?)"), "… && false", False),
    (re.compile(r"\btrue\s*\|\|"), "true || …", True),
    (re.compile(r"\|\|\s*true\b(?!\s*[=!]==?)"), "… || true", False),
    (re.compile(r"\b(?:if|while)\s*\(\s*(?:false|true|0|1)\s*\)"), "if (false) / if (0) 这类", False),
]


def test_no_switched_off_branches():
    """不许用「false && …」「if (false)」这类写法把代码关掉（看着像在用，其实永远不跑）；不要了就删掉。"""
    bad = []
    for f in _frontend_files():
        code = f.read_text(encoding="utf-8")
        for rx, what, after_compare_ok in SWITCHED_OFF:
            for m in rx.finditer(code):
                if after_compare_ok and re.search(r"[=!]==?\s*$", code[max(0, m.start() - 8):m.start()]):
                    continue
                bad.append(f"{f.relative_to(ROOT)}:{code.count(chr(10), 0, m.start()) + 1}: {what}")
    assert not bad, bad


# ── 元素 id、/static/ 引用 ────────────────────────────────────

# 脚本里引用了、页面模板里本来就没有的元素：都是脚本运行时自己生成的（测试会核对生成它的代码还在）
MADE_BY_SCRIPT = {
    "gmailSide": "core.js init()：写进左下角 #sysStatus 的 Gmail 那一行",
    "sideAuth": "core.js refreshGmail()：Gmail 没授权时生成的「授权」按钮",
    "posSelect": "mail.js renderInfo()：信息卡里的岗位类型下拉框",
    "wtAcctInput": "ws.js：改申请账号的弹窗里的输入框",
    "idcardInput": "profile.js loadIdcard()：证件号框里的输入框（没存过、或点了「换一个」时生成）",
    "idcardSave": "profile.js loadIdcard()：「存进钥匙串」按钮",
    "idcardChange": "profile.js loadIdcard()：存过以后的「换一个」按钮",
    "idcardForget": "profile.js loadIdcard()：存过以后的「删掉」按钮",
}
# 拼出来的 id：'#前缀' + 变量
DYNAMIC_PREFIX = {
    "tab-": "core.js switchTab()：'#tab-' + 页面名，页面名来自导航的 data-tab",
    "detail-": "board.js renderBoard()：看板每条记录下面的详情行，按记录 id 生成",
    "apx-": "apps.js renderApps()：按申请看时每张卡下面展开的岗位行，按申请 id 生成",
}
ID_REF = re.compile(r"""(?<=['"`(,\s])#([A-Za-z][\w-]*)(['"`]\s*\+)?""")   # '#xxx'、`#xxx …`、', #xxx'；后面跟 + 的是拼出来的
BY_ID = re.compile(r"""getElementById\(\s*(['"`])([\w-]+)\1""")
HEX = re.compile(r"[0-9a-fA-F]{3,8}")   # 颜色 #e6eeff 之类，不是 id


def test_element_ids_used_by_scripts_exist():
    p = _page()
    ids = set(p.ids)
    assert len(p.ids) == len(ids), "页面上有重复的 id"
    js = "\n".join(f.read_text(encoding="utf-8") for f in sorted(JS_DIR.glob("*.js")))
    missing = [m.group(2) for m in BY_ID.finditer(js) if m.group(2) not in ids and m.group(2) not in MADE_BY_SCRIPT]
    for m in ID_REF.finditer(js):
        name, concat = m.group(1), m.group(2)
        if HEX.fullmatch(name):
            continue
        if concat:
            assert name in DYNAMIC_PREFIX, f"拼出来的 id「#{name}…」没登记在 DYNAMIC_PREFIX 里"
        elif name not in ids and name not in MADE_BY_SCRIPT:
            missing.append(name)
    assert not missing, sorted(set(missing))
    for name in MADE_BY_SCRIPT:
        assert f'id="{name}"' in js, f"{name} 应该是脚本生成的，找不到生成它的代码"
    assert p.tabs and {"tab-" + t for t in p.tabs} <= ids                     # 每个 data-tab 都有 #tab-xxx
    assert 'id="detail-' in js
    acts = re.search(r"const ACTION_BTNS = \[([^\]]*)\]", js).group(1)        # core.js 里 '#' + id 拼的几个按钮
    assert set(re.findall(r"'(\w+)'", acts)) <= ids


def test_static_refs_are_served():
    p = _page()
    assert p.static and not re.search(r"\{[{%#]", p.html), "页面里还有没渲染的模板语法"
    c = panel.app.test_client()
    for url in p.static:
        r = c.get(url, headers=LOCAL)
        assert r.status_code == 200, url
        want = "javascript" if url.split("?")[0].endswith(".js") else "text/css"
        assert want in r.headers["Content-Type"], (url, r.headers["Content-Type"])
        r.close()


# ── 演示页 ────────────────────────────────────────────────────

def test_demo_build_is_one_file():
    """演示页 = 真实界面渲染后，把 /static/ 下的样式、脚本按加载顺序原样内联回来；虚构数据 mock.js 在所有脚本之前。"""
    html = _load("build_demo_for_test", ROOT / "scripts" / "build_demo.py").build()
    assert "/static/" not in html and not re.search(r"\{[{%#]", re.sub(r"<(script|style)>.*?</\1>", "", html, flags=re.S))
    assert "<title>求职投递面板 · 演示版</title>" in html
    assert (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8") in html
    pos = [html.index("<script>\n" + (JS_DIR / f).read_text(encoding="utf-8") + "</script>") for f in _page().scripts]
    assert pos == sorted(pos) and html.index('<script src="mock.js"></script>') < pos[0]


def test_public_sync_list_has_every_frontend_file(tmp_path):
    """公开版同步清单（scripts/sync_public.py 的 code_files：CODE 加上按目录收的 CODE_GLOBS）要有前端的每个文件和这个测试：
    少了的话公开版同步时演示页生成不出来（同步中途报错），推送前 --src 的隐私扫描也扫不到它。只按清单把文件拷进空目录，演示页照样能生成。"""
    sp = ROOT / "scripts" / "sync_public.py"
    if not sp.exists():
        pytest.skip("公开版没有 sync_public.py")
    code = _load("sync_public_for_list", sp).code_files(ROOT)
    want = [str(f.relative_to(ROOT)) for f in _frontend_files()] + [str(Path(__file__).resolve().relative_to(ROOT))]
    assert sorted(set(want) - set(code)) == []
    for rel in code:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, tmp_path / rel)
    html = _load("build_demo_public_copy", tmp_path / "scripts" / "build_demo.py").build()
    assert "/static/" not in html and (JS_DIR / "core.js").read_text(encoding="utf-8") in html


def test_static_urls_carry_a_version_that_changes(tmp_path):
    """页面上 /static/ 的网址都带 ?v=（静态文件最新修改时间）：部署换了文件，网址跟着变，浏览器不会用缓存里的旧脚本配新页面。"""
    import os
    import time
    p = _page()
    vs = {u.split("?v=")[1] for u in p.static if "?v=" in u}
    assert len(vs) == 1 and all("?v=" in u for u in p.static) and next(iter(vs)).isdigit(), p.static
    f = JS_DIR / "core.js"
    st = f.stat()
    try:
        os.utime(f, (st.st_atime, time.time() + 120))
        assert {u.split("?v=")[1] for u in _page().static if "?v=" in u} != vs
    finally:
        os.utime(f, (st.st_atime, st.st_mtime))
