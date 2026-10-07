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
  const VERSION = '1.2';
  if (window.__wsfill && window.__wsfill.version === VERSION) return;

  const ID = 'data-wsf-id';
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
  ].join(',');
  const ADD_WORDS = /^(?:\+\s*)?(?:添加|新增|增加|继续添加|再添加)/;
  const SUBMIT_WORDS = /提交|投递|申请职位|确认申请|预览并提交|完成申请/;

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  const flat = s => norm(s).replace(/[\s:：*＊]/g, '').toLowerCase();

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

  // 一个控件真正要操作的那个元素：自定义下拉 / 日期框以外层组件为准
  function controlRoot(el) {
    return el.closest(DATE_WRAP) || el.closest('.ant-select,.ant-cascader,.el-select,.el-cascader,.ivu-select,.ivu-cascader,.arco-select,.t-select,.n-select,.semi-select') || el;
  }

  function kindOf(el) {
    const root = controlRoot(el);
    if (el instanceof HTMLSelectElement) return 'select';
    if (el.type === 'radio' || el.getAttribute('role') === 'radio') return 'radio';
    if (el.type === 'checkbox' || el.getAttribute('role') === 'checkbox') return 'checkbox';
    if (el.type === 'file') return 'file';
    if (root.matches && root.matches(DATE_WRAP)) return 'date';
    if (/^(date|month)$/.test(el.type || '')) return 'date';
    if (root !== el || el.getAttribute('role') === 'combobox' || (el.readOnly && el.closest(SELECT_WRAP))) {
      return /cascader/i.test(root.className || '') ? 'cascader' : 'choice';
    }
    if (el instanceof HTMLTextAreaElement || el.isContentEditable) return 'textarea';
    return 'text';
  }

  function itemOf(el) {
    for (const sel of ITEM_EXACT) {
      const it = el.closest(sel);
      if (it) return it;
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
      const l = item.querySelector(LABEL_IN_ITEM);
      if (l && textOf(l) && textOf(l).length <= 40) return textOf(l);
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
      const sel = root.querySelector('.ant-select-selection-item,.ant-select-selection-selected-value,.el-select__selected-item,.ivu-select-selected-value,[class*="selection-item"],[class*="selected-value"]');
      if (sel) return textOf(sel);
      return norm(el.value || '');
    }
    if (el.isContentEditable) return norm(el.textContent);
    return norm(el.value || '');
  }

  function groupOf(el) {
    if (el.name && el.type) return Array.from(document.querySelectorAll(`input[type="${el.type}"][name="${CSS.escape(el.name)}"]`));
    const item = itemOf(el) || el.parentElement;
    return item ? Array.from(item.querySelectorAll('input[type=radio],input[type=checkbox],[role=radio],[role=checkbox]')) : [el];
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
  function fieldId(el) {
    let id = el.getAttribute(ID);
    if (!id) { id = 'w' + (++counter); el.setAttribute(ID, id); }
    return id;
  }

  function scan() {
    const seenGroups = new Set(), seenRoots = new Set(), fields = [];
    for (const el of document.querySelectorAll(CONTROL)) {
      if (!visible(el) && !visible(controlRoot(el))) continue;
      if (el.closest('[data-wsf-ignore]')) continue;
      const kind = kindOf(el);
      if (kind === 'file') continue;
      const root = controlRoot(el);
      if (seenRoots.has(root)) continue;
      seenRoots.add(root);
      let options;
      if (kind === 'radio' || kind === 'checkbox') {
        const group = groupOf(el);
        const key = group.map(x => fieldId(x)).join(',');
        if (seenGroups.has(key)) continue;
        seenGroups.add(key);
        group.forEach(x => seenRoots.add(x));
        options = group.map(choiceText).filter(Boolean);
      } else if (kind === 'select') {
        options = Array.from(el.options).map(o => norm(o.text)).filter(Boolean).slice(0, 60);
      }
      const f = {id: fieldId(root === el ? el : root), label: labelOf(el), section: '', kind,
                 value: currentValue(el, kind), required: required(el), _root: root};
      if (root !== el) el.setAttribute(ID + '-inner', f.id);
      if (options) f.options = options;
      const ph = el.getAttribute('placeholder');
      if (ph && ph !== f.label) f.placeholder = norm(ph);
      if (el.disabled || el.readOnly && kind === 'text' || el.getAttribute('aria-disabled') === 'true' || root.className.toString().includes('disabled')) f.disabled = true;
      fields.push(f);
    }
    const btnEls = Array.from(document.querySelectorAll('button,a,[role=button],span[class*="add"],div[class*="add"]'))
      .filter(b => visible(b) && ADD_WORDS.test(textOf(b)) && textOf(b).length <= 20);
    const secs = sectionMap(fields.map(f => f._root).concat(btnEls));
    fields.forEach(f => { f.section = secs.get(f._root) || ''; delete f._root; });
    const buttons = btnEls.map(b => ({id: fieldId(b), text: textOf(b), section: secs.get(b) || ''}));
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

  function press(el, key) {
    for (const type of ['keydown', 'keypress', 'keyup']) {
      el.dispatchEvent(new KeyboardEvent(type, {key, code: key, keyCode: key === 'Enter' ? 13 : key === 'Escape' ? 27 : 0, bubbles: true}));
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

  function visibleOptions() {
    return Array.from(document.querySelectorAll(OPTION)).filter(o => visible(o) && textOf(o) && textOf(o).length <= 80 &&
      !o.closest('[data-wsf-ignore]') && !/disabled/.test(o.className || '') && o.getAttribute('aria-disabled') !== 'true');
  }

  function bestOption(opts, want) {
    return opts.find(o => same(textOf(o), want) || same(o.getAttribute('title') || '', want)) ||
           opts.find(o => loose(textOf(o), want));
  }

  async function openChoice(root, inner) {
    const target = root.querySelector('.ant-select-selector,.el-input__inner,.el-select__wrapper,.ivu-select-selection,[class*="selector"],input') || root;
    click(target);
    if (inner && inner !== target) inner.focus();
    await sleep(250);
  }

  async function closeChoice(root) {
    const input = root.querySelector('input') || root;
    press(input, 'Escape');
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
    if (!o) {
      const search = root.querySelector('input:not([readonly])');
      if (search) {
        setNative(search, want);
        await sleep(300);
        o = bestOption(visibleOptions(), want);
      }
    }
    if (!o) o = await scrollFind(want);
    if (!o) { await closeChoice(root); return {ok: false, reason: '下拉里没找到「' + want + '」'}; }
    click(o);
    await sleep(160);
    const now = currentValue(inner || root, 'choice');
    if (now && !loose(now, want) && !loose(want, now)) return {ok: false, reason: '选完显示的是「' + now + '」'};
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

  // 日期框：能直接写的就写；只读的（antd 3 等）点开后在弹出日历自带的输入框里写，再回车
  const DATE_POPUP_INPUT = '.ant-calendar-input,.ant-picker-dropdown input,.ant-picker-panel input,.el-picker-panel input,.el-date-picker input,.ivu-date-picker-cells input';
  async function fillDate(root, value, inner) {
    const input = inner || root.querySelector('input') || root;
    const v = normDate(value);
    input.focus();
    click(input);
    await sleep(250);
    const pop = Array.from(document.querySelectorAll(DATE_POPUP_INPUT)).find(visible);
    const target = input.readOnly && pop ? pop : input;
    setNative(target, v);
    await sleep(80);
    press(target, 'Enter');
    await sleep(200);
    target.dispatchEvent(new Event('blur', {bubbles: true}));
    if (Array.from(document.querySelectorAll(DATE_POPUP_INPUT)).some(visible)) await closeChoice(root);
    const now = norm(input.value);
    if (!now) return {ok: false, reason: '日期没写进去（' + (input.readOnly ? '只读日期框' : '可写日期框') + '）'};
    return {ok: true, warning: normDate(now) === v ? '' : '日期显示为 ' + now};
  }

  // 自定义下拉框的全部选项（打开看一眼再关上）：拿不准选项原文时先看这个
  async function options(id) {
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

  async function fillOne(id, value) {
    const root = byId(id);
    if (!root) return {ok: false, reason: '页面上找不到这一项（可能页面变了，重新 scan）'};
    const inner = document.querySelector(`[${ID}-inner="${CSS.escape(id)}"]`) || (root.matches(CONTROL) ? root : root.querySelector('input,textarea'));
    const el = inner || root;
    const kind = kindOf(el);
    if (el.disabled || root.getAttribute('aria-disabled') === 'true') return {ok: false, reason: '这一项是灰的，不能填'};
    if (/身份证|证件号|护照号|银行卡/.test(labelOf(el))) return {ok: false, reason: '证件号码由本人自己填'};
    try {
      if (kind === 'radio' || kind === 'checkbox') return await fillChoiceGroup(el, value);
      if (kind === 'select') {
        const opt = Array.from(el.options).find(o => same(o.text, value)) || Array.from(el.options).find(o => loose(o.text, value));
        if (!opt) return {ok: false, reason: '下拉里没有「' + value + '」'};
        setNative(el, opt.value);
        return {ok: true};
      }
      if (kind === 'date') return await fillDate(root, value, inner);
      if (kind === 'cascader') return await fillCascader(root, value, inner);
      if (kind === 'choice') return await fillChoice(root, value, inner);
      el.focus();
      setNative(el, value);
      el.dispatchEvent(new Event('blur', {bubbles: true}));
      await sleep(30);
      return norm(el.value || el.textContent) === norm(value) ? {ok: true} : {ok: false, reason: '写进去后内容不一样（可能有字数上限）', now: norm(el.value).slice(0, 60)};
    } catch (e) {
      return {ok: false, reason: '出错：' + e.message};
    }
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
      mark(step.id, r.ok);
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

  window.__wsfill = {version: VERSION, scan, fill, fillOne, clickAdd, options};
})();
