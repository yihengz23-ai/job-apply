/*
 * wsfill.js —— 网申表单批量填写引擎（给求职投递面板里的助手用）
 *
 * 用法（在网申页面上执行）：
 *   await (0, eval)(await (await fetch('<本仓库 GitHub Pages 地址>/wsfill.js')).text());
 *   JSON.stringify(__wsfill.scan())            // 一次扫出整页能填的栏目（id、标题、分区、类型、选项、当前值）和「添加」按钮
 *   JSON.stringify(await __wsfill.fill([{id: 'w3', value: '汉族'}, ...]))   // 按计划批量填写，返回每项成功与否
 *   await __wsfill.clickAdd('a1')              // 点某个「添加教育经历」之类的按钮，再重新 scan
 *
 * 思路：先扫描、一次对照、脚本批量写入、填不上的标橙色留给人（不点任何提交按钮）。
 * 写值、点下拉选项、分级地区、单选按文字匹配的做法参考了 OpenJobAutofill（MIT License, Br1an67）。
 */
(() => {
  const VERSION = '2.34';
  if (window.__wsfill && window.__wsfill.version === VERSION && !window.__wsfillReload) return;   // 改脚本调试时先设 window.__wsfillReload = true

  const ID = 'data-wsf-id';
  const ID_LABEL = /身份证|证件号|护照号|银行卡/, ID_REFUSE = '证件号码由本人自己填';   // 证件号这一栏：不填、不清、不标框
  const CONTROL = [
    'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image])',
    'textarea', 'select', '[contenteditable="true"]', '[role="combobox"]', '[role="radio"]', '[role="checkbox"]',
  ].join(',');
  const SELECT_WRAP = '.ant-select,.ant-cascader,.el-select,.el-cascader,.ivu-select,.ivu-cascader,.arco-select,.t-select,.n-select,.semi-select,[class*="Select"],[class*="select"],[class*="picker"],[class*="Picker"]';
  const DATE_WRAP = '.ant-picker,.ant-calendar-picker,.el-date-editor,.ivu-date-picker,.arco-picker,.t-date-picker,[class*="date-picker"],[class*="DatePicker"]';
  // 一个栏目（标题 + 输入框）的外框：先认各组件库的准确类名，最后才用模糊匹配（模糊的要求里面真有标题）
  const ITEM_EXACT = ['.ant-form-item', '.el-form-item', '.ivu-form-item', '.arco-form-item', '.t-form__item', '.form-item'];
  const ITEM_FUZZY = '[class*="form-item"],[class*="formItem"],[class*="FormItem"],[class*="field-row"],[class*="field-wrapper"]';
  const LABEL_IN_ITEM = '.ant-form-item-label,.el-form-item__label,.ivu-form-item-label,.arco-form-item-label,.t-form__label,label,[class*="label"],[class*="Label"]';
  const OPTION = [
    '.ant-select-item-option', '.ant-cascader-menu-item', '.el-select-dropdown__item', '.el-cascader-node',
    '.ivu-select-item', '.ivu-cascader-menu-item', '.arco-select-option', '.t-select-option', '.n-base-select-option',
    '[role="option"]', '[class*="select-item"]', '[class*="dropdown-item"]', '[class*="option-item"]', 'li[class*="option"]',
    '[class*="Select-common-item"]', '[class*="Select-option"]', '[class*="Cascader-item"]', '[class*="sd-Menu-container"]',   // Moka（sd-* 组件，类名带随机后缀）
    '[class*="Dropdown-dropdown"] [class*="sd-Tag-container"]',   // Moka 地区选择（省份 / 城市 / 县区 分页里的标签按钮；输入框里已选的标签不算）
  ].join(',');
  const POPUP_LAYER = '.ant-calendar,.ant-picker-dropdown,.ant-select-dropdown,.el-picker-panel,.el-select-dropdown,.mtd-datepicker-pop,' +
    '[class*="datepicker-pop"],[class*="date-picker-pop"],[class*="picker-panel"],[class*="select-dropdown"],[class*="Dropdown-dropdown"]';
  const DROP_LAYER = '.el-select-dropdown,.ant-select-dropdown,.ivu-select-dropdown,.mtd-select-dropdown,.mtd-dropdown-menu,[class*="select-dropdown"],[class*="Dropdown-dropdown"],[role="listbox"]';
  const MULTI = '[class*="multiple"],[class*="select__tags"],[class*="multi_select"],[class*="multi-select"]';   // 多选下拉（Moka 的单选地区框也用标签样式，不能按标签认）
  const ADD_WORDS = /^(?:\+\s*)?(?:添加|新增|增加|继续添加|再添加)/;
  const SUBMIT_WORDS = /提交|投递|申请职位|确认申请|预览并提交|完成申请/;

  // 网申页在后台标签页（或屏幕休眠）时，浏览器会把 setTimeout 拖到 1 秒～1 分钟才跑一次，
  // 填一页要几十分钟。看不见的时候改用 MessageChannel 一轮轮让出、按真实时间等，不受这个节流影响。
  const yieldTask = () => new Promise(r => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  async function sleep(ms) {
    if (!document.hidden) return new Promise(r => setTimeout(r, ms));
    const end = performance.now() + ms;
    while (performance.now() < end) await yieldTask();
  }
  const norm = s => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  const flat = s => norm(s).replace(/[\s:：*＊]/g, '').toLowerCase();
  const clsOf = el => { const c = el.className; return String(c && c.baseVal != null ? c.baseVal : c || ''); };   // svg 的 className 是对象

  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  }

  function textOf(el) {
    if (!el) return '';
    const c = el.cloneNode(true);
    c.querySelectorAll('input,textarea,select,script,style,svg,button').forEach(n => n.remove());
    return norm(c.innerText || c.textContent || '');
  }

  // 一个控件真正要操作的那个元素：自定义下拉 / 日期框以外层组件为准。
  // 先认常见组件库，再按类名结尾通用识别（mtd-select、next-select、xx-date-picker……）
  // 也认 CSS Modules 那种带随机后缀的类名：sd-Select-container-1Eq4x、sd-Dropdown-container-1CigZ（Moka）
  // （随机后缀里至少有一个数字或大写字母，免得把 mtd-select-filter 这种子元素当成外层）
  //  不能整体加 i 标志：大小写不敏感时「含大写字母」这条就失效了）
  const HASH = '(?:[-_](?=[A-Za-z0-9]*[A-Z0-9])[A-Za-z0-9]{4,10})?';
  const WRAP_TOKEN = new RegExp('(?:^|\\s)(?:[A-Za-z][A-Za-z0-9]*[-_])+([Ss]elect|[Cc]ascader|[Pp]icker|[Dd]ate-editor|[Tt]ree-?[Ss]elect|[Dd]ropdown)' +
    '(?:[-_](?:container|wrapper|wrap|root))?' + HASH + '(?=\\s|$)');
  function controlRoot(el) {
    const known = el.closest(DATE_WRAP) || el.closest('.ant-select,.ant-cascader,.el-select,.el-cascader,.ivu-select,.ivu-cascader,.arco-select,.t-select,.n-select,.semi-select');
    if (known) return known;
    let cur = el.parentElement;
    for (let d = 0; cur && d < 5; d++, cur = cur.parentElement) {
      if (WRAP_TOKEN.test(String(cur.className || ''))) return cur;
    }
    return el;
  }
  function wrapType(root) {
    const m = String(root.className || '').match(WRAP_TOKEN);
    return m ? m[1].toLowerCase() : '';
  }

  function kindOf(el) {
    const root = controlRoot(el);
    if (el instanceof HTMLSelectElement) return 'select';
    if (el.type === 'radio' || el.getAttribute('role') === 'radio') return 'radio';
    if (el.type === 'checkbox' || el.getAttribute('role') === 'checkbox') return 'checkbox';
    if (el.type === 'file') return 'file';
    if (root.matches && root.matches(DATE_WRAP)) return 'date';
    const wt = root !== el ? wrapType(root) : '';
    if (/picker|date-editor/.test(wt) && !/color|time/.test(String(root.className))) return 'date';
    if (/cascader/.test(wt)) return 'cascader';
    if (/^(date|month)$/.test(el.type || '')) return 'date';
    if (wt === 'dropdown' && el.readOnly && /日期|生日|出生|date|birth/i.test(labelOf(el))) return 'date';
    if (root !== el || el.getAttribute('role') === 'combobox' || (el.readOnly && el.closest(SELECT_WRAP))) {
      return /cascader/i.test(root.className || '') ? 'cascader' : 'choice';
    }
    if (el instanceof HTMLTextAreaElement || el.isContentEditable) return 'textarea';
    return 'text';
  }

  const ITEM_TOKEN = /(?:^|\s)(?:[a-z][a-z0-9]*-)*form-item(?=\s|$)/i;
  const ITEM_HASHED = new RegExp('(?:^|\\s)(?:[A-Za-z][A-Za-z0-9]*[-_])*(?:form-?[Ii]tem|[Ff]orm[Ii]tem|[Ff]ield)[-_](?=[A-Za-z0-9]*[A-Z0-9])[A-Za-z0-9]{4,10}(?=\\s|$)');
  function itemOf(el) {
    for (const sel of ITEM_EXACT) {
      const it = el.closest(sel);
      if (it) return it;
    }
    for (let cur = el.parentElement, d = 0; cur && d < 10; d++, cur = cur.parentElement) {
      const c = String(cur.className || '');
      if (ITEM_TOKEN.test(c) || ITEM_HASHED.test(c)) return cur;
    }
    let cur = el.closest(ITEM_FUZZY);
    for (let d = 0; cur && d < 4; d++, cur = cur.parentElement && cur.parentElement.closest(ITEM_FUZZY)) {
      if (cur.querySelector(LABEL_IN_ITEM)) return cur;
    }
    return null;
  }

  // 分区标题：字号大（≥18px）或加粗的短文字（h1–h4 也算）；表单栏目标题、按钮、链接、下拉选项不算
  function headingText(el) {
    if (!(el instanceof HTMLElement) || el.children.length > 3) return '';
    const own = norm(Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join(''));
    const t = own || (el.children.length === 0 ? norm(el.textContent) : '');
    if (!t || t.length < 2 || t.length > 24 || /[:：]$/.test(t)) return '';
    if (el.closest('label,button,a,option,[role=option],[role=button],' + LABEL_IN_ITEM.replace(',label,[class*="label"],[class*="Label"]', ''))) return '';
    if (el.closest(OPTION)) return '';
    const st = getComputedStyle(el), fs = parseFloat(st.fontSize) || 0, fw = parseInt(st.fontWeight, 10) || 400;
    if (/^H[1-4]$/.test(el.tagName) || fs >= 18 || (fs >= 14 && fw >= 600)) return visible(el) ? t : '';
    return '';
  }

  // 按页面先后顺序走一遍：每个栏目归到它前面最近的标题下（「教育经历 > 教育经历2」这种保留两级）
  function sectionMap(roots) {
    const map = new Map(), want = new Set(roots);
    let major = '', minor = '', majorSize = 0;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    for (let n = walker.currentNode; n; n = walker.nextNode()) {
      if (want.has(n)) { map.set(n, minor && minor !== major ? major + ' > ' + minor : major); continue; }
      const h = headingText(n);
      if (!h) continue;
      const fs = parseFloat(getComputedStyle(n).fontSize) || 0;
      if (!major || fs >= majorSize - 1) { major = h; majorSize = fs; minor = ''; }
      else minor = h;
    }
    return map;
  }

  // 标题候选在不在栏目里面的某个下拉 / 日期组件里（选项文字、下拉菜单里的 label 不算标题）。
  // 栏目外框自己的类名带 select（Moka 的 select_info）不算。
  function inWidget(x, item) {
    const w = x.closest('[class*="select"],[class*="Select"],[class*="picker"],[class*="Picker"],[class*="cascader"],[class*="Cascader"],[class*="ropdown"]');
    return !!w && w !== item && item.contains(w);
  }

  function labelOf(el) {
    const id = el.id;
    if (id) {
      const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (l && textOf(l)) return textOf(l);
    }
    const aria = el.getAttribute('aria-label');
    if (aria) return norm(aria);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = lb.split(/\s+/).map(x => textOf(document.getElementById(x))).join(' ');
      if (norm(t)) return norm(t);
    }
    const item = itemOf(el);
    if (item) {
      const root = controlRoot(el);
      const l = Array.from(item.querySelectorAll('[class*="form-item-label"],.el-form-item__label,.t-form__label,' + LABEL_IN_ITEM + ',[class^="title"],[class*=" title"]'))
        .find(x => !root.contains(x) && !inWidget(x, item) && textOf(x) && textOf(x).length <= 40);
      if (l) return textOf(l).replace(/[*＊]\s*$/, '').replace(/^[*＊]\s*/, '');
      // 没有自己的标题（比如「起止时间」的第二个日期框）：用同一行前一个栏目的标题
      let prev = item.previousElementSibling;
      for (let k = 0; prev && k < 3; k++, prev = prev.previousElementSibling) {
        const pl = prev.querySelector && prev.querySelector('[class*="form-item-label"],' + LABEL_IN_ITEM);
        if (pl && textOf(pl)) return textOf(pl).replace(/[*＊]/g, '').trim() + '（后一项）';
      }
    }
    // 往上找，取控件前面最近的一段短文字
    let cur = controlRoot(el);
    for (let d = 0; cur && d < 4; d++, cur = cur.parentElement) {
      let sib = cur.previousElementSibling;
      while (sib) {
        const t = textOf(sib);
        if (t && t.length <= 40) return t;
        sib = sib.previousElementSibling;
      }
    }
    return norm(el.getAttribute('placeholder') || el.name || '');
  }

  function required(el) {
    const item = itemOf(el);
    return !!(el.required || el.getAttribute('aria-required') === 'true' ||
      (item && (item.querySelector('.ant-form-item-required,.is-required,[class*="required"]') || /[*＊]/.test((item.querySelector(LABEL_IN_ITEM) || {}).textContent || ''))));
  }

  function currentValue(el, kind) {
    if (kind === 'radio' || kind === 'checkbox') {
      const group = groupOf(el);
      return group.filter(x => x.checked || x.getAttribute('aria-checked') === 'true').map(choiceText).join('、');
    }
    if (kind === 'choice' || kind === 'cascader') {
      const root = controlRoot(el);
      const tags = Array.from(root.querySelectorAll('[class*="Input-tag"]:not(input):not([class*="tag-container"]):not([class*="tag-input"]),.el-select__tags-text,.ant-select-selection-item-content')).map(textOf).filter(Boolean);
      if (tags.length) return tags.join('、');
      const sel = root.querySelector('.ant-select-selection-item,.ant-select-selection-selected-value,.el-select__selected-item,.ivu-select-selected-value,[class*="selection-item"],[class*="selected-value"],[class*="filter-label"]:not([class*="hint"]),[class*="display-value"]');
      if (sel && textOf(sel)) return textOf(sel);
      const hint = root.querySelector('[class*="filter-hint"],[class*="select-placeholder"],[class*="selection-placeholder"]');
      if (hint && visible(hint)) return '';
      return norm(el.value || '');
    }
    if (el.isContentEditable) return norm(el.textContent);
    return norm(el.value || '');
  }

  function groupOf(el) {
    if (el.name && el.type) return Array.from(document.querySelectorAll(`input[type="${el.type}"][name="${CSS.escape(el.name)}"]`));
    const item = itemOf(el) || el.parentElement;
    if (!item) return [el];
    // Element UI 的 <label role="radio"> 里面还套着原生 radio：同一个选项只算一次（留原生的，它有 checked）
    return Array.from(item.querySelectorAll('input[type=radio],input[type=checkbox],[role=radio],[role=checkbox]'))
      .filter(x => !(x.getAttribute('role') && x.querySelector('input[type=radio],input[type=checkbox]')));
  }

  function choiceText(el) {
    const l = el.closest('label');
    if (l) return textOf(l);
    if (el.id) {
      const f = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (f) return textOf(f);
    }
    return norm(el.getAttribute('aria-label') || (el.nextElementSibling && textOf(el.nextElementSibling)) || el.value || '');
  }

  let counter = 0;
  const REG = {};
  // 换了新版本：旧版本留在页面上的编号清掉，免得新旧编号撞车
  document.querySelectorAll('[data-wsf-id],[data-wsf-id-inner]').forEach(e => { e.removeAttribute('data-wsf-id'); e.removeAttribute('data-wsf-id-inner'); });
  function fieldId(el) {
    let id = el.getAttribute(ID);
    if (!id) { id = 'w' + (++counter); el.setAttribute(ID, id); }
    return id;
  }

  // 扫描整页：先把要读的（可见、类型、标题、分区、当前值）全部读完，最后一次性打编号。
  // 一边读样式一边改页面会让浏览器反复重算整页样式，元素多的页面（有的大厂招聘站）会卡到超时。
  function scan() {
    const seenRoots = new Set(), rows = [];
    for (const el of document.querySelectorAll(CONTROL)) {
      if (seenRoots.has(el)) continue;
      // 单选 / 多选的小圆圈本身常是隐藏的，看外面包着的 label 显不显示
      const wrap = el.closest('label') || (el.parentElement && el.parentElement.closest('[class*="radio"],[class*="checkbox"]'));
      if (!visible(el) && !visible(controlRoot(el)) && !visible(wrap)) continue;
      if (el.closest('[data-wsf-ignore]')) continue;
      // <label role="radio"> 里套着原生 radio（Element UI）：只认里面的原生 radio
      if (/^(radio|checkbox)$/.test(el.getAttribute('role') || '') && el.querySelector('input[type=radio],input[type=checkbox]')) continue;
      // 弹出层里的输入框（日历自带的输入框、下拉的搜索框、关不掉的弹窗）不是表单栏目
      if (el.closest(LEAVING) || el.closest(POPUP_LAYER)) continue;
      const kind = kindOf(el);
      if (kind === 'file') continue;
      const root = controlRoot(el);
      if (seenRoots.has(root)) continue;
      seenRoots.add(root);
      let options;
      if (kind === 'radio' || kind === 'checkbox') {
        const group = groupOf(el);
        group.forEach(x => seenRoots.add(x));
        options = group.map(choiceText).filter(Boolean);
      } else if (kind === 'select') {
        options = Array.from(el.options).map(o => norm(o.text)).filter(Boolean).slice(0, 60);
      }
      const f = {id: '', label: labelOf(el), section: '', kind, value: currentValue(el, kind), required: required(el)};
      if (options) f.options = options;
      if ((kind === 'choice' || kind === 'cascader') && (root.matches(MULTI) || root.querySelector(MULTI) || root.getAttribute('aria-multiselectable') === 'true')) f.multi = true;
      const ph = el.getAttribute('placeholder');
      if (ph && ph !== f.label) f.placeholder = norm(ph);
      if (el.disabled || el.readOnly && kind === 'text' || el.getAttribute('aria-disabled') === 'true' || String(root.className).includes('disabled')) f.disabled = true;
      rows.push({f, el, root});
    }
    const byItem = new Map();
    for (const r of rows) {
      const it = itemOf(r.root);
      if (it) { if (!byItem.has(it)) byItem.set(it, []); byItem.get(it).push(r); }
    }
    for (const group of byItem.values()) {
      if (group.length < 2) continue;
      group.forEach((r, i) => {
        const hint = r.f.placeholder || (r.f.options && r.f.kind === 'checkbox' ? r.f.options.join('/') : '');
        r.f.label = `${r.f.label}[${i + 1}/${group.length}]` + (hint ? '·' + hint : '');
      });
    }
    // 证件号码不往外报（扫描结果会进对话记录）
    for (const r of rows) {
      if (/\d{15,18}[\dXx]?/.test(r.f.value) || (/证件号|身份证号|护照号/.test(r.f.label) && /\d{6,}/.test(r.f.value))) r.f.value = '（已填，证件号不显示）';
    }
    // 「添加」按钮：文字以添加 / 新增开头的；或者类名是 addBtn / add-btn、带加号图标的短按钮（有的网站是「＋ 工作/实习经历」）
    const ADD_CLASS = /(?:^|[-_\s])(?:add|Add)(?:[-_]?(?:btn|Btn|button|Button|item|more)|[A-Z0-9]|[-_\s]|$)/;
    const addLike = b => {
      const t = textOf(b);
      if (!t || t.length > 20 || SUBMIT_WORDS.test(t)) return false;
      return ADD_WORDS.test(t) || ADD_CLASS.test(clsOf(b)) || !!b.querySelector(':scope > [class*="plus"],:scope > [class*="icon-add"]');
    };
    let btnEls = Array.from(document.querySelectorAll('button,a,[role=button],span[class*="add"],div[class*="add"],span[class*="Add"],div[class*="Add"]'))
      .filter(b => visible(b) && addLike(b));
    btnEls = btnEls.filter(b => !btnEls.some(o => o !== b && o.contains(b)));   // 套在一起的只留外层
    const btnText = btnEls.map(textOf);
    const secs = sectionMap(rows.map(r => r.root).concat(btnEls));
    // 读完了，再统一写编号
    const nth = {};
    const fields = rows.map(({f, el, root}) => {
      f.id = fieldId(root);
      if (root !== el) el.setAttribute(ID + '-inner', f.id);
      f.section = secs.get(root) || '';
      const key = f.section + '|' + f.label;
      nth[key] = (nth[key] || 0) + 1;
      REG[f.id] = {key, n: nth[key]};     // 页面重排后元素会换掉：按「分区 + 标题 + 第几个」重新找回来
      return f;
    });
    const buttons = btnEls.map((b, i) => ({id: fieldId(b), text: btnText[i], section: secs.get(b) || ''}));
    return {version: VERSION, url: location.href, count: fields.length, fields, addButtons: buttons};
  }

  function byId(id) {
    return document.querySelector(`[${ID}="${CSS.escape(id)}"]`);
  }

  function setNative(el, value) {
    const v = value == null ? '' : String(value);
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
      : el instanceof HTMLInputElement ? HTMLInputElement.prototype : null;
    const desc = proto && Object.getOwnPropertyDescriptor(proto, 'value');
    if (el.isContentEditable) el.textContent = v;
    else if (desc && desc.set) desc.set.call(el, v);
    else el.value = v;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  }

  // 弹窗里的表单（有的网站每段经历一个弹窗）：按 Esc 会把整个弹窗关掉、填的内容丢，收下拉只能点空白处
  const MODAL = '[role=dialog],[aria-modal=true],.el-dialog,.ant-modal,.ivu-modal,.modal,.layui-layer,.van-popup,[class*="dialog"],[class*="Dialog"],[class*="modal"],[class*="Modal"]';
  function escape(el) {
    if (el && !(el.closest && el.closest(MODAL))) press(el, 'Escape');
  }

  function press(el, key) {
    for (const type of ['keydown', 'keypress', 'keyup']) {
      const keyCode = {Enter: 13, Escape: 27, ArrowDown: 40, ArrowUp: 38, Tab: 9, Backspace: 8}[key] || 0;
      el.dispatchEvent(new KeyboardEvent(type, {key, code: key, keyCode, which: keyCode, bubbles: true, cancelable: true}));
    }
  }

  // 后台标签页里 el.focus() 只改 activeElement、不发 focus 事件；Element 等组件靠 focus 事件打开日历 / 下拉，所以补发一个
  function focusOn(el) {
    if (!el || !el.focus) return;
    el.focus();
    if (!document.hasFocus()) {
      el.dispatchEvent(new FocusEvent('focus'));
      el.dispatchEvent(new FocusEvent('focusin', {bubbles: true}));
    }
  }

  function click(el) {
    if (!el) return false;
    el.scrollIntoView({block: 'center', inline: 'nearest'});
    const o = {bubbles: true, cancelable: true, view: window};
    el.dispatchEvent(new MouseEvent('mousedown', o));
    el.dispatchEvent(new MouseEvent('mouseup', o));
    el.click();
    return true;
  }

  const same = (a, b) => flat(a) === flat(b);
  const loose = (a, b) => { a = flat(a); b = flat(b); return !!a && !!b && (a === b || a.includes(b) || b.includes(a)); };

  // 选项套选项（Moka：sd-Select-common-item 里面还有 sd-Menu-container）时只留最里层：
  // 点外层的话，绑在里层的点击事件收不到；点里层会冒泡到外层，两种都管用
  function allOptions() {
    const all = Array.from(document.querySelectorAll(OPTION)).filter(o => visible(o) && textOf(o) && textOf(o).length <= 80 &&
      !o.closest('[data-wsf-ignore]') && !/disabled/.test(o.className || '') && o.getAttribute('aria-disabled') !== 'true' &&
      !o.closest(LEAVING) && !(o.offsetWidth === 0 || o.offsetHeight === 0));   // 0 大小的是给读屏软件用的隐藏选项（antd 5 的 aria 列表）
    const set = new Set(all);
    return all.filter(o => !Array.from(o.querySelectorAll(OPTION)).some(x => set.has(x)));
  }

  // 只认「现在操作的这个下拉」的选项。后台标签页里页面不渲染，关下拉的动画不执行，
  // 别的栏目点开过的菜单会一直留在页面上，选项混进来就会选错。所以：
  //   ① 先认控件旁边自己的菜单（Moka 的菜单就渲染在控件旁边）；
  //   ② 再认这次点开后新出现的（弹在页面最外层的菜单，antd / element 等）；
  //   ③ 别的栏目里面的菜单一律不算。
  //   ④ 都分不出来时，取离这个控件最近的那一组菜单。
  // 正在播关闭动画的菜单（类名带 leave，后台标签页里动画不走完会一直挂着）直接不算。
  const LEAVING = '[class*="-leave"],[class*="leave-active"],[class*="dropdown-hidden"],[class*="popup-hidden"],[class*="popover-hidden"]';   // 不能写 -hidden：overflow-hidden 这种工具类名到处都是
  let scope = null;
  function ownedBy(root) {
    const ids = [];
    for (const el of [root, ...root.querySelectorAll('[aria-controls],[aria-owns]')]) {
      for (const a of ['aria-controls', 'aria-owns']) (el.getAttribute(a) || '').split(/\s+/).filter(Boolean).forEach(x => ids.push(x));
    }
    return ids.map(x => document.getElementById(x)).filter(Boolean);
  }
  function popupOf(o) {
    for (let c = o.parentElement; c && c !== document.body; c = c.parentElement) {
      if (/dropdown|popup|popper|popover|menu|picker|listbox|select-pop/i.test(String(c.className || '') + ' ' + (c.getAttribute('role') || '')) &&
          c.getBoundingClientRect().height > 0 && !c.querySelector('[' + ID + ']')) return c;
    }
    return o;
  }
  function visibleOptions() {
    const all = allOptions();
    if (!scope) return all;
    for (let c = scope.root; c && c !== document.body; c = c.parentElement) {
      const mine = all.filter(o => c.contains(o));
      if (mine.length) return mine;
      if (c === scope.item) break;
    }
    const owned = ownedBy(scope.root).filter(x => x.offsetWidth > 0 && x.offsetHeight > 0);
    if (owned.length) {
      const o = all.filter(x => owned.some(p => p.contains(x)));
      if (o.length) return o;
    }
    const foreign = o => { const it = itemOf(o); return !!it && it !== scope.item; };
    const rest = all.filter(o => !foreign(o));
    const fresh = rest.filter(o => !scope.before.has(o));
    if (fresh.length) return fresh;
    if (!rest.length) return rest;
    // 按菜单分组，取离控件最近的一组
    const cr = scope.root.getBoundingClientRect();
    const groups = new Map();
    for (const o of rest) { const pp = popupOf(o); if (!groups.has(pp)) groups.set(pp, []); groups.get(pp).push(o); }
    let best = null, bestD = Infinity;
    for (const [pp, list] of groups) {
      const r = pp.getBoundingClientRect();
      const d = Math.max(0, r.top - cr.bottom, cr.top - r.bottom) + Math.abs(r.left - cr.left) / 4;
      if (d < bestD) { bestD = d; best = list; }
    }
    return best || rest;
  }

  function bestOption(opts, want) {
    return opts.find(o => same(textOf(o), want) || same(o.getAttribute('title') || '', want)) ||
           opts.find(o => loose(textOf(o), want));
  }

  // 打开下拉：点完确认选项真的出来了；没出来（可能刚才是开着的被点关了，或者组件只认焦点）就换个办法再试
  // 先把页面上所有开着的下拉关掉，免得旧下拉的选项混进来
  async function closeAll() {
    for (let i = 0; i < 2 && allOptions().length; i++) {
      const a = document.activeElement;
      if (a && a !== document.body) { escape(a); a.blur && a.blur(); }
      document.body.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
      document.body.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
      document.body.click();
      await sleep(150);
    }
  }

  // 下拉里能打字搜索的输入框。Element 的可搜索下拉没展开时输入框是只读的：先点一下让它展开再看
  async function searchBox(root) {
    let s = root.querySelector('input:not([type=hidden]):not([type=radio]):not([type=checkbox])');
    if (s && s.readOnly) {
      if (!looksOpen(root)) { click(s); await sleep(150); }
      if (s.readOnly) s = null;
    }
    return s;
  }
  const OPEN_MARK = '[class*="-open"],[class*="is-reverse"],.is-focus,[aria-expanded="true"]';   // Element 下拉展开时外框是 is-focus
  const looksOpen = root => root.matches(OPEN_MARK) || !!root.querySelector(OPEN_MARK);

  async function openChoice(root, inner) {
    await closeAll();
    scope = {root, item: itemOf(root), before: new Set(allOptions())};
    const target = root.querySelector('.ant-select-selector,.el-input__inner,.el-select__wrapper,.ivu-select-selection,[role="button"],[class*="selector"],[class*="select-filter"],input') || root;
    const input = inner || root.querySelector('input');
    const tries = [
      () => click(target),
      () => { if (input) { focusOn(input); click(input); } },
      () => { for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) target.dispatchEvent(new (t.startsWith('pointer') ? PointerEvent : MouseEvent)(t, {bubbles: true, cancelable: true, view: window, pointerType: 'mouse'})); },
      () => click(target),
    ];
    const popsBefore = new Set(Array.from(document.querySelectorAll(DROP_LAYER)).filter(shown));
    const freshPop = () => Array.from(document.querySelectorAll(DROP_LAYER)).some(d => shown(d) && !popsBefore.has(d) &&
      (scope.root.contains(d) || !d.closest('[' + ID + ']')));
    for (const tr of tries) {
      tr();
      for (let i = 0; i < 4; i++) {
        await sleep(90);
        if (visibleOptions().length) return true;
        if (freshPop() || looksOpen(root)) return true;
      }
    }
    return false;
  }

  // 清空一个下拉（点它的清除小叉；没有清除按钮就报告做不到）
  async function clear(id) {
    const root = byId(id);
    if (!root) return {ok: false, reason: '找不到这一项'};
    if (ID_LABEL.test(labelOf(inputOf(id) || root))) return {ok: false, reason: ID_REFUSE};
    // 清除小叉要鼠标悬停才出来；Element 的悬停事件绑在里层 .el-input 上（mouseenter 不往里传），所以里外都悬停一下
    const hoverOn = [root, ...root.querySelectorAll('.el-input,[class*="selector"],[class*="input-wrapper"],input')].slice(0, 6);
    for (const el of hoverOn) {
      el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: false}));
      el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
    }
    await sleep(150);
    const x = root.querySelector('[class*="clear"],[class*="close-circle"],[class*="circle-close"],[class*="error-circle"],.ant-select-clear');   // Element 是 el-icon-circle-close
    if (!x) return {ok: false, reason: '这一项没有清除按钮'};
    click(x);
    await sleep(200);
    await closeCalendars();
    const input = root.querySelector('input') || root;
    return {ok: !currentValue(input, kindOf(input) === 'date' ? 'date' : 'choice')};
  }

  async function closeChoice(root) {
    const input = root.querySelector('input') || root;
    escape(input);
    document.body.click();
    await sleep(80);
  }

  async function scrollFind(want, tries = 12) {
    let o = bestOption(visibleOptions(), want);
    if (o) return o;
    // 下拉列表很长（省份、民族）且不能搜索：在列表里往下滚着找
    const lists = Array.from(document.querySelectorAll('.rc-virtual-list-holder,.ant-select-dropdown .rc-virtual-list-holder,.el-select-dropdown__wrap,.el-scrollbar__wrap,.ivu-select-dropdown,[class*="dropdown"] [class*="list"],[role="listbox"]')).filter(visible);
    for (const list of lists) {
      list.scrollTop = 0;
      for (let i = 0; i < tries; i++) {
        await sleep(90);
        o = bestOption(visibleOptions(), want);
        if (o) return o;
        const before = list.scrollTop;
        list.scrollTop += Math.max(120, list.clientHeight * 0.8);
        list.dispatchEvent(new Event('scroll', {bubbles: true}));
        if (list.scrollTop === before) break;
      }
    }
    return null;
  }

  async function fillChoice(root, value, inner) {
    const want = norm(value);
    await openChoice(root, inner);
    let o = bestOption(visibleOptions(), want);
    const search = o ? null : await searchBox(root);
    if (!o && search) {
      focusOn(search);
      setNative(search, want);
      for (let i = 0; i < 15 && !o; i++) {   // 远程搜索：最多等 3 秒
        await sleep(200);
        o = bestOption(visibleOptions(), want);
        if (!o && i === 4) press(search, 'ArrowDown');
        if (!o && i === 9 && !looksOpen(root)) click(search);
      }
    }
    if (!o) o = await scrollFind(want);
    if (!o) {
      if (search && search.value) setNative(search, '');
      await closeChoice(root);
      return {ok: false, reason: '下拉里没找到「' + want + '」'};
    }
    click(o);
    await sleep(160);
    let now = currentValue(inner || root, 'choice');
    if (!now) { await sleep(300); now = currentValue(inner || root, 'choice'); }
    if (!now) return {ok: false, reason: '点了「' + textOf(o) + '」，但框里没显示出来'};
    if (!loose(now, want) && !loose(want, now)) return {ok: false, reason: '选完显示的是「' + now + '」'};
    return {ok: true};
  }

  // 省 / 市 / 区这种分级选择：值写成「浙江/金华」或数组
  async function fillCascader(root, value, inner) {
    const parts = Array.isArray(value) ? value : String(value).split(/[\/>｜|,，、]/).map(norm).filter(Boolean);
    await openChoice(root, inner);
    for (const part of parts) {
      const o = (await scrollFind(part)) || bestOption(visibleOptions(), part.replace(/[省市区县]$/, ''));
      if (!o) { await closeChoice(root); return {ok: false, reason: '分级选项里没找到「' + part + '」'}; }
      click(o);
      await sleep(260);
    }
    await sleep(120);
    return {ok: true};
  }

  // 日期框，三步依次试，哪步写对了就停：
  //   ① 能直接写的框直接写；② 只读的（antd 3 等）在弹出日历自带的输入框里写再回车；
  //   ③ 都不行（MTD 这类只能点的日历）：在弹出日历上翻年份 / 月份，再点格子
  const DATE_POPUP_INPUT = '.ant-calendar-input,.ant-picker-dropdown input,.ant-picker-panel input,.el-picker-panel input,.el-date-picker input,.ivu-date-picker-cells input';
  const CAL_POP = '.mtd-datepicker-pop,.ant-calendar,.ant-picker-dropdown,.el-picker-panel,.ivu-picker-panel-body,.arco-picker-container,.t-date-picker__panel,.n-date-panel,.semi-datepicker,' +
    '[class*="datepicker-pop"],[class*="date-picker-pop"],[class*="picker-panel"],[class*="calendar-panel"],[class*="picker-dropdown"],[class*="calendar"]';
  const MONTHS_EN = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
  const MONTHS_CN = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二'];
  const NOW_WORDS = /^(至今|至今日|今|现在|当前|present|now|till now|until now)$/i;
  const PREV_YEAR = /super-prev|prev-year|year-prev|left-switcher|d-arrow-left|double-?left|fast-backward|arrow-double-left|prev-double|chevrons-left|angle-double-left|上一年|前一年|«/i;
  const NEXT_YEAR = /super-next|next-year|year-next|right-switcher|d-arrow-right|double-?right|fast-forward|arrow-double-right|next-double|chevrons-right|angle-double-right|下一年|后一年|»/i;
  const PREV_ONE = /prev|arrow-left|chevron-left|angle-left|left-btn|icon-left|上个?月|‹/i;
  const NEXT_ONE = /next|arrow-right|chevron-right|angle-right|right-btn|icon-right|下个?月|›/i;

  // 日历里的格子和按钮：不滚动页面地点（滚动会让有的日历自己关掉）
  function tap(el) {
    const o = {bubbles: true, cancelable: true, view: window};
    for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) el.dispatchEvent(new (t.startsWith('pointer') ? PointerEvent : MouseEvent)(t, o));
    el.click();
  }

  function monthOf(t) {   // 「5月」「五月」「May」→ 5
    t = norm(t).toLowerCase();
    let m = t.match(/^(\d{1,2})\s*月$/);
    if (m) return +m[1] >= 1 && +m[1] <= 12 ? +m[1] : null;
    m = t.match(/^([一二三四五六七八九十]{1,3})月$/);
    if (m) return MONTHS_CN.indexOf(m[1]) + 1 || null;
    if (/^[a-z]{3,9}\.?$/.test(t)) { const i = MONTHS_EN.indexOf(t.slice(0, 3)); if (i >= 0) return i + 1; }
    return null;
  }

  // 弹层出来没有：不看透明度和缩放（后台标签页里入场动画走不完，弹层一直是透明 / 压扁的），看显示状态和布局尺寸
  function shown(el) {
    if (!el || !el.isConnected) return false;
    const st = getComputedStyle(el);
    return st.display !== 'none' && st.visibility !== 'hidden' && el.offsetWidth > 0 && !el.closest(LEAVING);
  }
  function calendars() {
    const pops = Array.from(document.querySelectorAll(CAL_POP)).filter(p => {
      if (/^(I|SVG|INPUT)$/i.test(p.tagName) || !shown(p)) return false;
      return p.offsetWidth >= 150 && p.offsetHeight >= 100 && !p.querySelector('[' + ID + ']');
    });
    return pops.filter(p => !pops.some(q => q !== p && q.contains(p)));
  }

  async function closeCalendars() {
    for (let i = 0; i < 3 && calendars().length; i++) {
      const a = document.activeElement;
      if (a && a !== document.body) { escape(a); a.blur && a.blur(); }
      document.body.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
      document.body.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
      document.body.click();
      await sleep(150);
    }
  }

  async function openCalendar(input) {
    await closeCalendars();
    const before = new Set(calendars());
    focusOn(input);
    click(input);
    for (let i = 0; i < 10; i++) {
      await sleep(80);
      const now = calendars();
      const fresh = now.filter(p => !before.has(p));
      if (fresh.length) return fresh[fresh.length - 1];
      if (i === 5 && now.length) return now[now.length - 1];
    }
    return calendars().pop() || null;
  }

  // 读日历现在显示的年 / 月，以及月份格子（选月的日历）或日子格子（选日的日历）
  function calState(pop) {
    const st = {year: null, month: null, mode: '', months: [], days: []};
    const leaves = Array.from(pop.querySelectorAll('*')).filter(e => e.children.length === 0 && visible(e));
    const inView = pop.querySelector('[class*="in-view"]');
    for (const e of leaves) {
      const t = norm(e.textContent);
      if (!t || t.length > 14) continue;
      let m;
      if ((m = t.match(/^((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月$/))) { if (st.year == null) { st.year = +m[1]; st.month = +m[2]; } continue; }
      if ((m = t.match(/^([A-Za-z]{3,9})\.?\s+((?:19|20)\d{2})$/)) && monthOf(m[1])) { if (st.year == null) { st.year = +m[2]; st.month = monthOf(m[1]); } continue; }
      if ((m = t.match(/^((?:19|20)\d{2})\s*年?$/))) { if (st.year == null) st.year = +m[1]; continue; }
      // 格子自己和外面两层的类名：不能选的（disabled）、上个月 / 下个月露出来的日子都跳过
      const chain = [e, e.parentElement, e.parentElement && e.parentElement.parentElement].filter(x => x && x !== pop);
      const cls = chain.map(clsOf).join(' ');
      const off = /disabled|forbid/i.test(cls) || chain.some(x => x.getAttribute('aria-disabled') === 'true');
      const mo = monthOf(t);
      if (mo) { st.months.push({n: mo, el: e, off}); continue; }
      if (/^\d{1,2}$/.test(t) && +t >= 1 && +t <= 31) {
        const other = inView ? !/in-view/.test(cls) : /prev|next|last-month|other|outside|not-current/i.test(cls);
        st.days.push({n: +t, el: e, off: off || other});
      }
    }
    st.mode = st.months.length >= 12 ? 'month' : st.days.length >= 28 ? 'day' : '';
    if (st.mode === 'day' && st.month == null && st.months.length) st.month = st.months[0].n;
    return st;
  }

  // 翻页按钮：按元素自己的类名 / 提示文字 / 符号认「上一年 / 下一年 / 上个月 / 下个月」。
  // 只看元素自己（不看里面的子元素）：认到的是按钮里的小图标也没关系，点它会冒泡到按钮上
  function calArrows(pop) {
    const out = {};
    for (const b of pop.querySelectorAll('*')) {
      if (/\d/.test(b.textContent) || b.querySelectorAll('*').length > 3 || !visible(b) || b.getBoundingClientRect().width > 60) continue;
      const own = Array.from(b.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('');
      const sig = [clsOf(b), norm(own), b.getAttribute('aria-label') || '', b.getAttribute('title') || ''].join(' ');
      const type = PREV_YEAR.test(sig) ? 'prevYear' : NEXT_YEAR.test(sig) ? 'nextYear' : PREV_ONE.test(sig) ? 'prevOne' : NEXT_ONE.test(sig) ? 'nextOne' : '';
      if (type && !out[type]) out[type] = b;
    }
    return out;
  }

  async function stepTo(pop, read, target, prev, next) {
    for (let i = 0; i < 80; i++) {
      const cur = read();
      if (cur == null) return false;
      if (cur === target) return true;
      const b = cur > target ? prev : next;
      if (!b) return false;
      tap(b);
      await sleep(50);
      if (read() === cur) { await sleep(150); if (read() === cur) return false; }
    }
    return false;
  }

  async function pickCalendar(input, value) {
    const pop = await openCalendar(input);
    if (!pop) return '点开后没有出现日历';
    if (NOW_WORDS.test(norm(value))) {
      const now = Array.from(pop.querySelectorAll('*')).find(e => e.children.length === 0 && visible(e) && NOW_WORDS.test(norm(e.textContent)));
      if (!now) return '日历里没有「至今」（找旁边的「至今」勾选框）';
      tap(now);
      await sleep(250);
      return '';
    }
    const m = normDate(value).match(/^(\d{4})-(\d{2})(?:-(\d{2}))?$/);
    if (!m) return '日期格式看不懂：' + value;
    const [y, mo, d] = [+m[1], +m[2], m[3] ? +m[3] : 1];
    let st = calState(pop);
    if (!st.mode) return '认不出这个日历的格子';
    let ar = calArrows(pop);
    const yearPrev = ar.prevYear || (st.mode === 'month' ? ar.prevOne : null);
    const yearNext = ar.nextYear || (st.mode === 'month' ? ar.nextOne : null);
    if (yearPrev || yearNext) {
      if (!(await stepTo(pop, () => calState(pop).year, y, yearPrev, yearNext))) return '日历翻不到 ' + y + ' 年';
    }
    st = calState(pop);
    if (st.mode === 'month') {
      if (st.year !== y) return '日历翻不到 ' + y + ' 年';
      const cell = st.months.find(c => c.n === mo && !c.off);
      if (!cell) return '日历里 ' + mo + ' 月不能选';
      tap(cell.el);
    } else {
      ar = calArrows(pop);
      const ym = () => { const s = calState(pop); return s.year == null || s.month == null ? null : s.year * 12 + s.month; };
      if (!(await stepTo(pop, ym, y * 12 + mo, ar.prevOne, ar.nextOne))) return '日历翻不到 ' + y + ' 年 ' + mo + ' 月';
      const cell = calState(pop).days.find(c => c.n === d && !c.off);
      if (!cell) return '日历里 ' + d + ' 号不能选';
      tap(cell.el);
    }
    await sleep(250);
    // 有的日历选完还要点「确定」（只在日历弹窗里找，不碰页面上的按钮）
    if (pop.isConnected && visible(pop)) {
      const ok = Array.from(pop.querySelectorAll('button,a,[role=button],span')).find(b => visible(b) && /^(确定|确认|ok)$/i.test(norm(b.textContent)));
      if (ok) { tap(ok); await sleep(200); }
    }
    return '';
  }

  const sameDate = (shown, want) => { const a = normDate(shown); return !!a && (a === want || want.startsWith(a + '-') || a.startsWith(want + '-')); };

  async function fillDate(root, value, inner) {
    const input = inner || root.querySelector('input') || root;
    const v = normDate(value);
    const done = () => NOW_WORDS.test(norm(value)) ? NOW_WORDS.test(norm(input.value)) : sameDate(input.value, v);
    if (done()) return {ok: true};
    if (!NOW_WORDS.test(norm(value))) {
      focusOn(input);
      click(input);
      await sleep(250);
      const pop = Array.from(document.querySelectorAll(DATE_POPUP_INPUT)).find(visible);
      const target = input.readOnly && pop ? pop : input;
      if (!input.readOnly || pop) {
        setNative(target, v);
        await sleep(80);
        press(target, 'Enter');
        await sleep(200);
        target.dispatchEvent(new Event('blur', {bubbles: true}));
      }
      await closeCalendars();
      if (done()) return {ok: true};
    }
    const why = await pickCalendar(input, value);
    await closeCalendars();
    if (done()) return {ok: true};
    const now = norm(input.value);
    return {ok: false, reason: (why || '日期没改过来') + (now ? '（现在显示 ' + now + '）' : '')};
  }

  // 自定义下拉框的全部选项（打开看一眼再关上）：拿不准选项原文时先看这个
  async function options(id) {
    scope = null;
    const root = byId(id);
    if (!root) return {ok: false, reason: '找不到这一项'};
    const inner = document.querySelector(`[${ID}-inner="${CSS.escape(id)}"]`) || (root.matches(CONTROL) ? root : root.querySelector('input'));
    if (root instanceof HTMLSelectElement) return {ok: true, options: Array.from(root.options).map(o => norm(o.text)).filter(Boolean)};
    await openChoice(root, inner);
    const seen = [];
    const add = () => visibleOptions().forEach(o => { const t = textOf(o); if (!seen.includes(t)) seen.push(t); });
    add();
    const lists = Array.from(document.querySelectorAll('.rc-virtual-list-holder,.el-select-dropdown__wrap,.el-scrollbar__wrap,.ivu-select-dropdown,[class*="dropdown"] [class*="list"],[role="listbox"]')).filter(visible);
    for (const list of lists) {
      list.scrollTop = 0;
      for (let i = 0; i < 30; i++) {
        const before = list.scrollTop;
        list.scrollTop += Math.max(120, list.clientHeight * 0.8);
        list.dispatchEvent(new Event('scroll', {bubbles: true}));
        await sleep(70);
        add();
        if (list.scrollTop === before) break;
      }
    }
    await closeChoice(root);
    return {ok: true, options: seen.slice(0, 200)};
  }

  function normDate(value) {
    const t = norm(value);
    let m = t.match(/(\d{4})\D(\d{1,2})\D(\d{1,2})/);
    if (m) return `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`;
    m = t.match(/(\d{4})\D(\d{1,2})/);
    if (m) return `${m[1]}-${m[2].padStart(2, '0')}`;
    return t;
  }

  async function fillChoiceGroup(el, value) {
    const wants = Array.isArray(value) ? value : [value];
    const group = groupOf(el);
    let hit = 0;
    for (const want of wants) {
      const target = group.find(x => same(choiceText(x), want)) || group.find(x => loose(choiceText(x), want)) ||
        (/^(是|yes|true)$/i.test(want) && group.find(x => /是|yes/i.test(choiceText(x)))) ||
        (/^(否|no|false)$/i.test(want) && group.find(x => /否|no/i.test(choiceText(x))));
      if (target) {
        if (!(target.checked || target.getAttribute('aria-checked') === 'true')) click(target.closest('label') || target);
        hit++;
      }
    }
    return hit ? {ok: true} : {ok: false, reason: '选项里没有「' + wants.join('、') + '」'};
  }

  function relocate(id) {
    const want = REG[id];
    if (!want) return null;
    const s = scan();
    const nth = {};
    for (const f of s.fields) {
      const key = f.section + '|' + f.label;
      nth[key] = (nth[key] || 0) + 1;
      if (key === want.key && nth[key] === want.n) return f.id;
    }
    return null;
  }

  async function fillOne(id, value) {
    scope = null;
    let root = byId(id);
    if (!root) {   // 前面选了某项后页面重新排了（比如选完学历多出「学制」）：重新扫一遍，按栏目名对回来
      const nid = relocate(id);
      root = nid && byId(nid);
      if (root) id = nid;
    }
    if (!root) return {ok: false, reason: '页面上找不到这一项（可能页面变了，重新 scan）'};
    const inner = document.querySelector(`[${ID}-inner="${CSS.escape(id)}"]`) || (root.matches(CONTROL) ? root : root.querySelector('input,textarea'));
    const el = inner || root;
    const kind = kindOf(el);
    if (el.disabled || root.getAttribute('aria-disabled') === 'true') return {ok: false, reason: '这一项是灰的，不能填'};
    if (ID_LABEL.test(labelOf(el))) return {ok: false, reason: ID_REFUSE};
    try {
      if (kind === 'radio' || kind === 'checkbox') return await fillChoiceGroup(el, value);
      if (kind === 'select') {
        const opt = Array.from(el.options).find(o => same(o.text, value)) || Array.from(el.options).find(o => loose(o.text, value));
        if (!opt) return {ok: false, reason: '下拉里没有「' + value + '」'};
        setNative(el, opt.value);
        return {ok: true};
      }
      if (kind === 'date') return await fillDate(root, value, inner);
      if (kind === 'cascader' || (kind === 'choice' && /[\/>]/.test(String(value)))) return await fillCascader(root, value, inner);
      if (kind === 'choice') return await fillChoice(root, value, inner);
      focusOn(el);
      setNative(el, value);
      el.dispatchEvent(new Event('blur', {bubbles: true}));
      await sleep(30);
      return norm(el.value || el.textContent) === norm(value) ? {ok: true} : {ok: false, reason: '写进去后内容不一样（可能有字数上限）', now: norm(el.value).slice(0, 60)};
    } catch (e) {
      return {ok: false, reason: '出错：' + e.message};
    }
  }

  // 证件号这一栏（本人自己手动填，助手不碰）：只有网站不填它就进不了下一步时，助手用 hasValue 问一句「填了没有」，不回内容。
  // 本人是一位一位打的：光标还在这一栏、或者身份证位数不对，而且跟上次看时不一样，就回「还在输」；没再变了才算「已填」
  function inputOf(id) {
    let root = byId(id);
    if (!root) { const nid = relocate(id); root = nid && byId(nid); if (root) id = nid; }
    if (!root) return null;
    return document.querySelector(`[${ID}-inner="${CSS.escape(id)}"]`) || (root.matches(CONTROL) ? root : root.querySelector('input,textarea')) || root;
  }
  const typing_ = {};
  function hasValue(id) {
    const el = inputOf(id);
    if (!el) return '找不到';
    const v = String(currentValue(el, kindOf(el)) || '').replace(/\s/g, '');
    if (!v) { delete typing_[id]; return '空'; }
    const sig = v.length + ':' + [...v].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);   // 只记长度和摘要，不留号码
    const changed = typing_[id] !== sig;
    typing_[id] = sig;
    const focused = document.activeElement === el || el.contains(document.activeElement);
    const short = /身份证/.test(labelOf(el)) && v.length !== 15 && v.length !== 18;
    return changed && (focused || short) ? '还在输' : '已填';
  }

  function mark(id, ok) {
    const el = byId(id);
    if (!el) return;
    el.style.outline = ok ? '2px solid #22c55e' : '2px solid #f97316';
    el.style.outlineOffset = '1px';
  }

  async function fill(plan) {
    const results = [];
    for (const step of plan || []) {
      if (!step || step.id == null) continue;
      const r = await fillOne(step.id, step.value);
      if (r.reason !== ID_REFUSE) mark(step.id, r.ok);   // 证件号那一栏不标框
      results.push({id: step.id, ...r});
      await sleep(40);
    }
    const failed = results.filter(r => !r.ok);
    return {done: results.length - failed.length, failed};
  }

  async function clickAdd(id) {
    const b = byId(id);
    if (!b) return {ok: false, reason: '找不到这个按钮'};
    if (SUBMIT_WORDS.test(textOf(b))) return {ok: false, reason: '这是提交类按钮，不点'};
    click(b);
    await sleep(500);
    return {ok: true};
  }

  // 在后台跑一批（不受浏览器工具单次 45 秒的限制）：start 立刻返回，用 progress() 查进度
  const job_ = {running: false, total: 0, done: [], failed: [], current: ''};
  function start(plan) {
    if (job_.running) return {ok: false, reason: '上一批还在跑'};
    Object.assign(job_, {running: true, total: (plan || []).length, done: [], failed: [], current: ''});
    (async () => {
      for (const step of plan || []) {
        if (!step || step.id == null) continue;
        job_.current = step.id + ' ← ' + String(step.value).slice(0, 20);
        let r;
        try {
          r = step.clear ? await clear(step.id) : await fillOne(step.id, step.value);
        } catch (e) { r = {ok: false, reason: '出错：' + e.message}; }
        if (r.reason !== ID_REFUSE) mark(step.id, r.ok);
        (r.ok ? job_.done : job_.failed).push({id: step.id, value: step.value, ...r});
        await sleep(60);
      }
      job_.running = false;
      job_.current = '';
    })();
    return {ok: true, total: job_.total};
  }
  const progress = () => JSON.parse(JSON.stringify(job_));

  // 带搜索的下拉：输入关键字，看服务器给出哪些候选项（不选）
  async function peek(id, text) {
    scope = null;
    const root = byId(id);
    if (!root) return {ok: false, reason: '找不到这一项'};
    const inner = document.querySelector(`[${ID}-inner="${CSS.escape(id)}"]`) || root.querySelector('input');
    await openChoice(root, inner);
    const search = await searchBox(root);
    if (search) setNative(search, text);
    let opts = [];
    for (let i = 0; i < 12; i++) {
      await sleep(200);
      opts = visibleOptions().map(textOf);
      if (opts.length) break;
    }
    if (search) setNative(search, '');
    await closeChoice(root);
    return {ok: true, options: opts.slice(0, 40)};
  }

  // ── 给助手看的文字版 ──────────────────────────────────────────
  // 浏览器工具一次只回传约 1000 个字，超出的部分直接截掉。所以给助手看的都是紧凑文字、按行分页：
  // view() 看整页栏目，more() 翻下一页，opts(id) 看下拉选项，job() 看后台批次进度。
  const PAGE = 940;
  let pages = [], pageAt = 0;
  function paginate(text) {
    pages = [];
    let cur = '';
    for (const line of String(text).split('\n')) {
      const l = line.length > PAGE ? line.slice(0, PAGE - 1) + '…' : line;
      if (cur && cur.length + 1 + l.length > PAGE) { pages.push(cur); cur = ''; }
      cur = cur ? cur + '\n' + l : l;
    }
    if (cur) pages.push(cur);
    pageAt = 0;
    return more();
  }
  function more() {
    if (pageAt >= pages.length) return '（没有更多了）';
    const pg = pages[pageAt++];
    if (pages.length === 1) return pg;
    return pg + `\n〔第 ${pageAt}/${pages.length} 页` + (pageAt < pages.length ? '，接着执行 __wsfill.more()〕' : '，完〕');
  }
  // 整页栏目：每行「编号 标题｜类型(*必填)(灰)｜{选项}＝现在的值」，分区变了就插一行「## 分区」
  //   view({empty: true}) 只看还空着的；view({section: '教育'}) 只看某个分区
  function view(o = {}) {
    const s = scan();
    let fs = s.fields;
    if (o.empty) fs = fs.filter(f => !f.value && !f.disabled);
    if (o.section) fs = fs.filter(f => f.section.includes(o.section));
    const out = [`共 ${s.count} 项` + (fs.length !== s.count ? `，下面列 ${fs.length} 项` : '') +
      '；添加按钮：' + (s.addButtons.map(b => b.id + '「' + b.text + '」' + (b.section ? '@' + b.section.split(' > ').pop() : '')).join(' ') || '无')];
    let sec = null;
    for (const f of fs) {
      const short = f.section.split(' > ').pop() || '（无分区）';
      if (short !== sec) { out.push('## ' + short); sec = short; }
      const v = f.kind === 'textarea' ? (f.value ? f.value.length + '字' : '') : f.value.slice(0, 30);
      out.push(`${f.id} ${f.label.slice(0, 24)}｜${f.kind}${f.multi ? '(多选)' : ''}${f.required ? '*' : ''}${f.disabled ? '(灰)' : ''}` +
        (f.options ? '{' + f.options.join('/').slice(0, 60) + '}' : '') + '＝' + v);
    }
    return paginate(out.join('\n'));
  }
  async function opts(id) {
    const r = await options(id);
    return paginate(r.ok ? `${id} 的选项（共 ${r.options.length} 个）：` + r.options.join(' / ') : r.reason);
  }
  async function peekText(id, text) {
    const r = await peek(id, text);
    return paginate(r.ok ? `搜「${text}」出来：` + (r.options.join(' / ') || '（没有候选项）') : r.reason);
  }
  function job() {
    const head = job_.running ? `进行中 ${job_.done.length + job_.failed.length}/${job_.total}，正在 ${job_.current}` : `已结束：成功 ${job_.done.length}/${job_.total}`;
    const fails = job_.failed.map(f => `${f.id}（${String(f.value).slice(0, 12)}）：${f.reason}`);
    return paginate(head + (fails.length ? '\n失败：\n' + fails.join('\n') : '，没有失败'));
  }
  async function fillText(plan) {
    const r = await fill(plan);
    return paginate(`成功 ${r.done} 项` + (r.failed.length ? '；失败：\n' + r.failed.map(f => `${f.id}：${f.reason}`).join('\n') : '，没有失败'));
  }

  // ── 读回：本人提交以后，把网站上「实际提交的内容 / 投递进度」原样发回面板，记进投递看板 ──
  // 只读，不碰页面。在线简历这种表单页：按分区整理成「标题：内容」（起止时间两个框合成一行，长文本保留换行）；
  // 投递记录、申请详情这种只读页：取页面正文。都附上页面上看到的附件文件名。证件号一律打码。
  // 面板地址：面板发这段脚本时把占位换成自己的地址（测试环境是 5002）；没换（旧面板、直接读文件）就用正式面板
  const PANEL_INJECTED = '__PANEL_BASE__';
  const PANEL = /^https?:\/\//.test(PANEL_INJECTED) ? PANEL_INJECTED : 'http://localhost:5001';
  const hideIds = s => String(s == null ? '' : s)
    .replace(/(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])/g, '[证件号已隐去]')
    .replace(/(?<![0-9A-Za-z])\d{15}(?![0-9A-Za-z])/g, '[证件号已隐去]');
  function detectAccount() {
    const t = document.body.innerText || '';
    const hi = t.match(/(?:你好|您好|Hi|Hello)[，,：:\s]*([^\s，,。！!]{3,40})/);
    if (hi && /[\d*@]/.test(hi[1])) return hi[1];
    const m = t.match(/1\d{2}\*{3,6}\d{2,4}/) || t.match(/[\w.+-]{1,4}\*{2,}[\w.+-]*@[\w-]+\.[\w.]+/);
    return m ? m[0] : '';
  }
  function fileNames() {
    const t = document.body.innerText || '';
    return Array.from(new Set(t.match(/[^\s\\/:*?"<>|，,；;]{1,80}\.(?:pdf|docx?|pptx?|xlsx?|png|jpe?g|zip)(?![A-Za-z])/gi) || [])).slice(0, 20);
  }
  // 页头、页脚、导航、侧边栏：标签、role，或者类名 / id 里带 header、footer、nav（CSS Modules 的 footer___xx 也算）
  const CHROME_CLS = /(?:^|[-_\s])(?:footer|header|navbar|nav|topbar|sidebar|side-?menu)(?:[-_\s]|$)/i;
  const isChrome = el => /^(HEADER|FOOTER|NAV|ASIDE)$/.test(el.tagName) || /^(banner|contentinfo|navigation)$/.test(el.getAttribute('role') || '') ||
    CHROME_CLS.test(clsOf(el)) || CHROME_CLS.test(el.id || '');
  // 正文所在的那块：从 body 往下走，页头页脚不算；只有一个子块装着几乎全部正文时才走进去，免得丢内容
  function mainRoot() {
    let el = document.body;
    for (let depth = 0; depth < 20; depth++) {
      const kids = Array.from(el.children).filter(k => !/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(k.tagName) && visible(k) && !isChrome(k));
      const lens = kids.map(k => (k.innerText || '').trim().length);
      const sum = lens.reduce((a, b) => a + b, 0);
      const i = lens.indexOf(Math.max(0, ...lens));
      if (!sum || i < 0 || sum - lens[i] > 20) break;   // 旁边的块只剩二十来个字（侧边菜单之类）才走进去
      el = kids[i];
    }
    return el;
  }
  function pageText() {
    const root = mainRoot();
    let t = root.innerText || '';
    for (const el of root.querySelectorAll('header,footer,nav,aside,[role=banner],[role=contentinfo],[role=navigation],[class*="footer"],[class*="Footer"],[class*="header"],[class*="Header"]')) {
      if (!isChrome(el)) continue;
      const x = visible(el) && (el.innerText || '').trim();
      if (x && x.length < t.length / 2) t = t.replace(x, '');
    }
    return t.split('\n').map(l => l.trim()).filter(Boolean).join('\n');
  }
  function snapshot() {
    const s = scan(), lines = [];
    let sec = null, prev = null, filled = 0;
    for (const f of s.fields) {
      const root = byId(f.id);
      let v = f.value;
      if (root && f.kind === 'textarea') {
        const ta = root.tagName === 'TEXTAREA' ? root : root.querySelector('textarea');
        if (ta && ta.value.trim()) v = ta.value.trim();
      } else if (root && (f.kind === 'text' || f.kind === 'date')) {   // 起止时间（ant-picker-range）一个组件两个框
        const ins = Array.from(root.querySelectorAll('input')).filter(i => !/^(hidden|file|radio|checkbox)$/.test(i.type));
        if (ins.length > 1) v = ins.map(i => norm(i.value)).filter(Boolean).join(' ~ ');
      }
      if (!v) continue;
      filled++;
      const short = f.section.split(' > ').pop() || '';
      if (short !== sec) { lines.push('【' + (short || '其他') + '】'); sec = short; prev = null; }
      const base = f.label.replace(/\[\d+\/\d+\].*$/, '');
      if (prev && prev.base === base && /\[\d+\/\d+\]/.test(f.label)) { prev.vals.push(v); lines[prev.at] = base + '：' + prev.vals.join(' '); continue; }
      prev = {base, vals: [v], at: lines.length};
      lines.push(base + '：' + v);
    }
    const files = fileNames(), account = detectAccount();
    const mode = filled >= 5 ? 'form' : 'page';
    let text = mode === 'form' ? lines.join('\n') : pageText() + (filled ? '\n【页面上的表单】\n' + lines.join('\n') : '');
    if (files.length) text += '\n【页面上的附件】' + files.join('；');
    return {mode, filled, account, files, text: hideIds(text).slice(0, 30000)};
  }
  function snapshotText() {
    const s = snapshot();
    return paginate(`（${s.mode === 'form' ? '表单 ' + s.filled + ' 项有内容' : '页面正文'}，${s.text.length} 字${s.account ? '，账号 ' + s.account : ''}）\n` + s.text);
  }
  // 照面板发来的那句话执行：await __wsfill.readback({task, key, kind: 'status' | 'resume' | 'jd', status, account, position, location, positions})
  async function readback(o = {}) {
    if (!o.task || !o.key) return '缺 task 或 key：照面板发来的那句话原样执行';
    const s = snapshot();
    const body = {key: o.key, kind: o.kind || 'resume', url: location.href, title: document.title, text: s.text,
      status: o.status || '', account: o.account || s.account, position: o.position || '', location: o.location || '',
      positions: o.positions || ''};
    let r, d = {};
    try {
      r = await fetch((o.base || PANEL) + '/api/wsreadback/' + encodeURIComponent(o.task),
        {method: 'POST', headers: {'Content-Type': 'text/plain'}, body: JSON.stringify(body)});
      d = await r.json().catch(() => ({}));
    } catch (e) { return '没发上：连不上面板（' + e.message + '）'; }
    if (!r.ok) return '没发上：' + (d.error || r.status);
    return `已记进看板：${body.kind === 'status' ? '投递记录 / 进度' : body.kind === 'jd' ? '岗位 JD「' + body.position + '」' : '提交的内容'}，${s.text.length} 字` +
      (s.mode === 'form' ? `（表单 ${s.filled} 项有内容）` : '（页面正文）') + (body.account ? `；账号 ${body.account}` : '') +
      (d.record ? '' : '；看板记录等标成已提交后自动带过去');
  }

  // ── 标记：这个网页是面板里哪个对话在填 ──
  // 网页四周一圈颜色框 + 顶上一个「面板助手 · 公司」小标签，颜色和面板「网申」页那一行、对话框一样，几个助手同时干活时一眼分得清。
  // 盖在 <html> 上的一层（不在 body 里，读回 / 扫描都不会读到它），点不到、不挡操作；记在这个标签页的 sessionStorage 里，换页后重新加载脚本自己带上。
  const MARK_ID = 'wsf-mark';
  function markPage(o = {}) {
    const color = /^#[0-9a-fA-F]{3,8}$/.test(o.color || '') ? o.color : '#2563eb';
    const label = String(o.label || '').replace(/[<>&"']/g, '').slice(0, 20);
    try { sessionStorage.setItem('__wsfill_mark', JSON.stringify({color, label})); } catch (e) {}
    let el = document.getElementById(MARK_ID);
    if (!el) {
      el = document.createElement('div');
      el.id = MARK_ID;
      el.setAttribute('data-wsf-ignore', '');
      document.documentElement.appendChild(el);
    }
    el.style.cssText = `position:fixed;inset:0;border:5px solid ${color};pointer-events:none;z-index:2147483647;box-sizing:border-box`;
    el.innerHTML = `<div style="position:absolute;top:0;left:50%;transform:translateX(-50%);background:${color};color:#fff;` +
      `font:bold 12px/1.7 -apple-system,sans-serif;padding:0 12px;border-radius:0 0 8px 8px;white-space:nowrap">面板助手 · ${label}</div>`;
    return `已标记：${label}`;
  }
  try { const m = JSON.parse(sessionStorage.getItem('__wsfill_mark') || 'null'); if (m && m.label) markPage(m); } catch (e) {}

  window.__wsfill = {version: VERSION, scan, fill, fillOne, clickAdd, options, clear, start, progress, peek,
    view, more, opts, peekText, job, fillText, snapshot, snapshotText, readback, mark: markPage, hasValue};
})();
