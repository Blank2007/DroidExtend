/* The page half of the shell: draw what Python says, ask Python to act.
   No framework -- the whole UI is a handful of sections and one popup, and a
   local WebView2 answers in under a millisecond. */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
let S = { snap: null, seq: 0, page: "picture", view: "main", pop: null, maxed: false };

function api() { return (window.pywebview && window.pywebview.api) || null; }
async function call(name, ...args) {
  const a = api();
  if (!a) return null;
  try { return await a[name](...args); } catch (e) { console.error(name, e); return null; }
}

/* ------------------------------------------------------------------ drawing */
function setText(id, v) { const e = document.getElementById(id); if (e) e.textContent = v; }
function setDot(kind) {
  const d = $("#dot");
  d.className = "dot" + (kind && kind !== "idle" ? " " + kind : "");
}
function renderMain(s) {
  setText("ver", "v" + (s.version || ""));
  setText("stat", s.statText || "");
  setText("dev", s.device || "");
  const note = $("#note");
  note.textContent = s.note || "";
  note.style.display = s.note ? "" : "none";
  setDot(s.dot);
  const m = s.metrics || {};
  setText("mCap", m.cap === "-" ? "—" : m.cap);
  setText("mSend", m.send === "-" ? "—" : m.send);
  setText("mPhone", m.phone === "-" ? "—" : m.phone);
  setText("mLat", m.lat === "-" ? "—" : m.lat);
  setText("dispPath", s.display || "虚拟显示器 —");
  setText("gpuName", s.gpu || "采集显卡 —");
  setText("summary", "当前配置：" + (s.summary || ""));
  const f = s.form || {};
  $("#selRes").querySelector("b").textContent = f.res || "—";
  $("#selHz").querySelector("b").textContent = (f.hz || "") + " Hz";
  $("#selFps").querySelector("b").textContent = (f.fps || "") + " fps";
  $("#selBr").querySelector("b").textContent = f.bitrate || "—";
  $("#customRow").classList.toggle("hidden", !f.custom);
  if (f.custom) {
    if (document.activeElement !== $("#inpW")) $("#inpW").value = f.w;
    if (document.activeElement !== $("#inpH")) $("#inpH").value = f.h;
  }
  const running = !!s.streaming;
  const busy = !!s.busy;
  $("#btnStart").disabled = running || busy;
  $("#btnStop").disabled = !running && !(s.stat === "connecting");
  document.body.classList.toggle("light", (f.theme === "light") ||
    (f.theme === "auto" && window.matchMedia("(prefers-color-scheme: light)").matches));
}

/* --------------------------------------------------------------------- log */
function addLogs(lines) {
  const body = $("#logBody");
  if (!body || !lines || !lines.length) return;
  const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 30;
  const frag = document.createDocumentFragment();
  for (const l of lines) {
    const d = document.createElement("div");
    d.className = "ln";
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = l.t;
    d.appendChild(t);
    const m = document.createElement("span");
    m.className = l.tag || "";
    m.textContent = l.m;
    d.appendChild(m);
    frag.appendChild(d);
  }
  body.appendChild(frag);
  while (body.childElementCount > 800) body.removeChild(body.firstChild);
  if (atBottom) body.scrollTop = body.scrollHeight;
}

async function pump() {
  const snap = await call("snapshot");
  if (snap && !snap.error) { S.snap = snap; renderMain(snap); offerUpdate(snap.update); offerAsk(snap.ask); }
  if (S.snap) {
    const lines = await call("logs", S.seq);
    if (lines && lines.length) {
      S.seq = lines[lines.length - 1].n;
      addLogs(lines);
    }
  }
}

/* ------------------------------------------------------------------ popups */
function closePop() { $("#pop").classList.add("hidden"); S.pop = null; }
function openPop(anchor, items, onPick) {
  const pop = $("#pop");
  pop.innerHTML = "";
  for (const it of items) {
    const d = document.createElement("div");
    d.className = "pi" + (it.on ? " on" : "");
    d.textContent = it.label;
    d.onclick = () => { closePop(); onPick(it.value); };
    pop.appendChild(d);
  }
  pop.classList.remove("hidden");
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.round(r.left) + "px";
  pop.style.top = Math.round(r.bottom + 4) + "px";
  pop.style.minWidth = Math.round(r.width) + "px";
  S.pop = anchor;
}
const LISTS = {
  res: (f) => f.resList, hz: (f) => f.hzList, fps: (f) => f.fpsList,
  bitrate: (f) => f.bitrateList, quality: (f) => f.qualityList,
  preset: (f) => f.presetList, encoder: (f) => f.encList,
  backend: (f) => f.backendList, position: (f) => f.positionList,
  rc: (f) => f.rcList, vddOffMode: (f) => f.vddOffModeList,
};
function itemLabel(x) {
  if (x && typeof x === "object") return x.zh || x.label || x.v;
  return String(x);
}
function itemValue(x) { return (x && typeof x === "object") ? x.v : x; }

function bindSelects() {
  for (const el of $$(".sel[data-k]")) {
    el.addEventListener("mousedown", (e) => {
      e.stopPropagation();
      const f = (S.snap && S.snap.form) || {};
      const get = LISTS[el.dataset.k];
      if (!get) return;
      const items = get(f) || [];
      const cur = el.dataset.k === "res" ? f.res : f[el.dataset.k];
      openPop(el, items.map((x) => ({
        label: itemLabel(x), value: itemValue(x), on: String(itemValue(x)) === String(cur),
      })), (v) => call("act", "set", { field: el.dataset.k, value: v }));
    });
  }
}

/* ------------------------------------------------------------------ window */
function bindWindow() {
  const tb = $("#tb");
  tb.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || e.target.closest(".wbtn")) return;
    call("drag", 2);
  });
  tb.addEventListener("dblclick", (e) => {
    if (e.target.closest(".wbtn")) return;
    call("win", "max");
    S.maxed = !S.maxed;
    document.body.classList.toggle("maxed", S.maxed);
  });
  for (const el of $$("#edges .e")) {
    el.addEventListener("mousedown", (e) => {
      e.stopPropagation();
      if (e.button === 0) call("drag", parseInt(el.dataset.hit, 10));
    });
  }
  $("#btnMin").onclick = () => call("win", "min");
  $("#btnMax").onclick = () => {
    call("win", "max");
    S.maxed = !S.maxed;
    document.body.classList.toggle("maxed", S.maxed);
  };
  $("#btnClose").onclick = async () => {
  const r = await call("win", "close");
  if (r === "ask") askClose();
};
  $("#btnMenu").onclick = () => showView(S.view === "settings" ? "main" : "settings");
}

/* ---------------------------------------------------------------- settings */
const NAV = [
  ["picture", "画面"], ["codec", "编码与画质"], ["device", "设备"],
  ["boot", "启动与退出"], ["notify", "通知"], ["look", "外观"],
  ["manual", "使用说明"], ["about", "关于"],
];
function showView(v) {
  S.view = v;
  $("#main").classList.toggle("hidden", v !== "main");
  $("#settings").classList.toggle("hidden", v !== "settings");
  if (v === "settings") renderNav();
}
function renderNav() {
  const nav = $("#nav");
  nav.innerHTML = "";
  for (const [k, label] of NAV) {
    const b = document.createElement("button");
    b.className = "navitem" + (k === S.page ? " on" : "");
    b.textContent = label;
    b.onclick = () => { S.page = k; renderNav(); renderPage(); };
    nav.appendChild(b);
  }
  renderPage();
}
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function line(k, v) {
  const d = el("div", "line");
  d.appendChild(el("div", "k", k));
  const vv = el("div", "v");
  if (typeof v === "string") vv.appendChild(el("div", "note", v));
  else vv.appendChild(v);
  d.appendChild(vv);
  return d;
}
function section(title) {
  const s = el("div", "sec");
  s.appendChild(el("div", "st", title));
  return s;
}
function selectBox(key, text) {
  const d = el("div", "sel");
  d.dataset.k = key;
  d.appendChild(el("b", null, text));
  d.appendChild(el("i", "caret"));
  return d;
}
function renderPage() {
  const box = $("#pages");
  box.innerHTML = "";
  const f = (S.snap && S.snap.form) || {};
  if (S.page === "picture") renderPicture(box, f);
  else if (S.page === "codec") renderCodec(box, f);
  else if (S.page === "device") renderDevice(box, f);
  else if (S.page === "boot") renderBoot(box, f);
  else if (S.page === "notify") renderNotify(box, f);
  else if (S.page === "look") renderLook(box, f);
  else if (S.page === "manual") renderManual(box, f);
  else if (S.page === "about") renderAbout(box, f);
  bindSelects();
}
function radio(name, value, cur, label, onPick) {
  const d = el("label", "rad");
  const i = document.createElement("input");
  i.type = "radio"; i.name = name; i.value = value; i.checked = value === cur;
  i.onchange = () => onPick(value);
  d.appendChild(i);
  d.appendChild(el("span", null, label));
  return d;
}
function check(key, on, label) {
  const d = el("label", "chk");
  const i = document.createElement("input");
  i.type = "checkbox"; i.checked = !!on;
  i.onchange = () => call("act", "set", { field: key, value: i.checked });
  d.appendChild(i);
  d.appendChild(el("span", null, label));
  return d;
}
function renderPicture(box, f) {
  const s1 = section("显示模式");
  const r = el("div", "rads");
  r.appendChild(radio("mode", "extend", f.mode, "扩展显示器（多一块屏）",
    (v) => call("act", "set", { field: "mode", value: v })));
  r.appendChild(radio("mode", "mirror", f.mode, "镜像（手机显示电脑桌面）",
    (v) => call("act", "set", { field: "mode", value: v })));
  s1.appendChild(line("模式", r));
  s1.appendChild(line("", check("matchPhone", f.matchPhone, "跟着手机面板走（推荐）")));
  box.appendChild(s1);

  const s2 = section("屏幕尺寸");
  const res = el("div", "fields");
  res.appendChild(selectBox("res", f.res));
  s2.appendChild(line("分辨率", res));
  if (f.custom) {
    const w = el("div", "dual");
    const iw = el("input", "inp"); iw.value = f.w; iw.id = "inpW";
    const x = el("span", "x", "x");
    const ih = el("input", "inp"); ih.value = f.h; ih.id = "inpH";
    w.appendChild(iw); w.appendChild(x); w.appendChild(ih);
    s2.appendChild(line("自定义尺寸", w));
  }
  const hz = el("div", "fields");
  hz.appendChild(selectBox("hz", f.hz + " Hz"));
  hz.appendChild(selectBox("fps", f.fps + " fps"));
  s2.appendChild(line("刷新率 / 帧率", hz));
  box.appendChild(s2);

  const s3 = section("屏幕位置");
  const pos = el("div", "fields");
  pos.appendChild(selectBox("position", labelOf(f.positionList, f.position)));
  s3.appendChild(line("放在哪边", pos));
  if (f.position === "custom") {
    const w = el("div", "dual");
    const ix = el("input", "inp"); ix.value = f.posX; ix.dataset.k = "posX";
    const iy = el("input", "inp"); iy.value = f.posY; iy.dataset.k = "posY";
    w.appendChild(el("span", "x", "X")); w.appendChild(ix);
    w.appendChild(el("span", "x", "Y")); w.appendChild(iy);
    s3.appendChild(line("自定义坐标", w));
  }
  box.appendChild(s3);
}
function labelOf(list, v) {
  for (const it of (list || [])) if (String(itemValue(it)) === String(v)) return itemLabel(it);
  return String(v);
}
function renderCodec(box, f) {
  const s1 = section("编码器");
  const e = el("div", "fields");
  e.appendChild(selectBox("encoder", labelOf(f.encList, f.encoder)));
  s1.appendChild(line("编码器", e));
  const p = el("div", "fields");
  p.appendChild(selectBox("preset", f.preset));
  s1.appendChild(line("预设", p));
  box.appendChild(s1);

  const s2 = section("码率");
  const rc = el("div", "fields");
  rc.appendChild(selectBox("rc", labelOf(f.rcList, f.rc)));
  s2.appendChild(line("码率控制", rc));
  const br = el("div", "fields");
  br.appendChild(selectBox("bitrate", f.bitrate + " Mbps"));
  s2.appendChild(line("码率上限", br));
  const q = el("div", "fields");
  q.appendChild(selectBox("quality", "Q " + f.quality));
  s2.appendChild(line("画质", q));
  box.appendChild(s2);
}
function renderDevice(box, f) {
  const s1 = section("采集与触控");
  const be = el("div", "fields");
  be.appendChild(selectBox("backend", labelOf(f.backendList, f.backend)));
  s1.appendChild(line("采集方式", be));
  s1.appendChild(line("", check("drawMouse", f.drawMouse, "显示鼠标指针")));
  s1.appendChild(line("", check("touch", f.touch, "手机触摸控制电脑鼠标")));
  s1.appendChild(line("", check("reinstallApk", f.reinstallApk, "每次重新安装手机端 APK")));
  box.appendChild(s1);

  const s2 = section("虚拟显示器");
  const info = el("div", "note");
  const drv = (S.snap && S.snap.driver) || {};
  info.textContent = S.snap ? (S.snap.display || "未检测到虚拟显示器") : "—";
  s2.appendChild(line("显示器", info));
  const btns = el("div", "btns");
  const b1 = el("button", "btn ghost", "安装 / 修复驱动");
  b1.onclick = () => call("act", "installDriver");
  const b2 = el("button", "btn ghost", "卸载驱动");
  b2.onclick = () => call("act", "uninstallDriver");
  btns.appendChild(b1); btns.appendChild(b2);
  s2.appendChild(line("", btns));
  box.appendChild(s2);

  const s3 = section("诊断与工具");
  const b3 = el("div", "btns");
  const tl = el("button", "btn ghost", "测一下数据线速度");
  tl.onclick = () => call("act", "linkspeed");
  const dg = el("button", "btn ghost", "诊断连接");
  dg.onclick = () => call("act", "diagnose");
  const orph = el("button", "btn ghost", "清理残留的虚拟屏");
  orph.onclick = () => call("act", "cleanupOrphans");
  const off = el("button", "btn ghost", "彻底关闭虚拟屏");
  off.onclick = () => call("act", "vddOff");
  const usb = el("button", "btn ghost", "安装 USB 驱动");
  usb.onclick = () => call("act", "installUsbDriver");
  const apk = el("button", "btn ghost", "重装手机端 App");
  apk.onclick = () => call("act", "installApk");
  b3.append(tl, dg, orph, off, usb, apk);
  s3.appendChild(line("", b3));
  box.appendChild(s3);
}
function renderBoot(box, f) {
  const s1 = section("开机");
  s1.appendChild(line("", check("autostart", f.autostart, "开机自动启动 DroidExtend")));
  s1.appendChild(line("", check("autoStream", f.autoStream, "开机后自动开始投屏")));
  const w = el("div", "fields");
  w.appendChild(selectBoxWait(f.autoStreamWait));
  s1.appendChild(line("等手机多久", w));
  box.appendChild(s1);

  const s2 = section("关闭窗口");
  const ca = el("div", "rads");
  for (const [v, label] of [["ask", "每次问我"], ["tray", "收进托盘"], ["quit", "直接退出"]]) {
    ca.appendChild(radio("closeAction", v, f.closeAction, label,
      (x) => call("act", "set", { field: "closeAction", value: x })));
  }
  s2.appendChild(line("关闭按钮", ca));
  box.appendChild(s2);

  const s3 = section("退出时对虚拟屏");
  const off = el("div", "rads");
  for (const it of (f.vddOffModeList || [])) {
    off.appendChild(radio("vddOffMode", it.v, f.vddOffMode, it.zh,
      (x) => call("act", "set", { field: "vddOffMode", value: x })));
  }
  s3.appendChild(line("", off));
  box.appendChild(s3);
}
function selectBoxWait(cur) {
  const d = el("div", "sel");
  d.dataset.k = "autoStreamWait";
  d.appendChild(el("b", null, cur === 0 ? "一直等" : ("等 " + cur + " 秒")));
  d.appendChild(el("i", "caret"));
  return d;
}
function renderNotify(box, f) {
  const s1 = section("Windows 通知");
  s1.appendChild(line("", check("notify", (f.notify || {}).master,
    "用系统通知告诉我这些事")));
  const kinds = [["stream", "开机自动投屏开始"], ["manual", "我按开始投屏"],
    ["fail", "投屏失败或中断"], ["driver", "虚拟显示器驱动事件"],
    ["update", "有新版本"], ["apk", "手机端 App 需要更新"], ["misc", "其它"]];
  for (const [k, label] of kinds) {
    const c = document.createElement("input");
    c.type = "checkbox";
    c.checked = !!(f.notify || {})[k];
    c.onchange = () => {
      const nv = Object.assign({}, f.notify || {});
      nv[k] = c.checked;
      call("act", "set", { field: "notify", value: nv });
    };
    const d = el("label", "chk");
    d.appendChild(c);
    d.appendChild(el("span", null, label));
    s1.appendChild(line("", d));
  }
  const t = el("button", "btn ghost", "发一条测试通知");
  t.onclick = () => call("act", "testNotify");
  s1.appendChild(line("", t));
  box.appendChild(s1);
}
function renderLook(box, f) {
  const s1 = section("颜色");
  const t = el("div", "rads");
  for (const [v, label] of [["auto", "跟随系统"], ["light", "浅色"], ["dark", "深色"]]) {
    t.appendChild(radio("theme", v, f.theme, label,
      (x) => call("act", "set", { field: "theme", value: x })));
  }
  s1.appendChild(line("", t));
  box.appendChild(s1);
}
function renderManual(box, f) {
  const s1 = section("上手三步");
  const steps = el("div", "note");
  steps.innerHTML =
    "1. 手机或平板上装好 DroidExtend App —— 第一次连接时电脑会自动装。<br>" +
    "2. 用 USB 数据线连到电脑，手机上允许 USB 调试。<br>" +
    "3. 点「开始投屏」。虚拟显示器驱动没装过的话，先在「设备」页装一次。";
  s1.appendChild(line("", steps));
  box.appendChild(s1);
  const s2 = section("完整说明");
  const n = el("div", "note");
  n.textContent = "画质和延迟怎么取舍、连不上怎么查、触摸反控和托盘的行为，" +
    "都写在完整说明里。";
  s2.appendChild(line("", n));
  const b = el("div", "btns");
  const open = el("button", "btn primary", "打开使用说明");
  open.onclick = () => openDoc("manual");
  const home = el("button", "btn ghost", "项目主页");
  home.onclick = () => call("act", "open_url", { url: "https://github.com/Blank2007/DroidExtend" });
  const iss = el("button", "btn ghost", "反馈问题");
  iss.onclick = () => call("act", "open_url", { url: "https://github.com/Blank2007/DroidExtend/issues" });
  b.append(open, home, iss);
  s2.appendChild(line("", b));
  box.appendChild(s2);
}
function renderAbout(box, f) {
  const s1 = section("版本");
  s1.appendChild(line("程序", "DroidExtend " + ((S.snap && S.snap.version) || "")));
  s1.appendChild(line("手机端", "随程序一起分发，连接时自动安装"));
  const b1 = el("div", "btns");
  const check = el("button", "btn ghost", "检查更新");
  check.onclick = () => call("act", "checkUpdate");
  const log = el("button", "btn ghost", "更新日志");
  log.onclick = () => openDoc("changelog");
  const abt = el("button", "btn ghost", "关于本程序");
  abt.onclick = () => openDoc("about");
  b1.append(check, log, abt);
  s1.appendChild(line("", b1));
  box.appendChild(s1);
  const s2 = section("第三方组件");
  s2.appendChild(line("许可", el("div", "note",
    "虚拟显示器驱动（MIT）、FFmpeg（GPL）、adb（Apache）等，许可原文随程序一起分发。")));
  const b2 = el("div", "btns");
  const lic = el("button", "btn ghost", "第三方组件许可");
  lic.onclick = () => openDoc("license");
  b2.appendChild(lic);
  s2.appendChild(line("", b2));
  box.appendChild(s2);
  const s3 = section("链接");
  const wrap = el("div", "btns col2");
  const LINKS = [["GitHub 仓库", "https://github.com/Blank2007/DroidExtend"],
    ["问题反馈", "https://github.com/Blank2007/DroidExtend/issues"],
    ["最新版本", "https://github.com/Blank2007/DroidExtend/releases"],
    ["使用说明", "https://github.com/Blank2007/DroidExtend#readme"]];
  for (const [label, url] of LINKS) {
    const a = el("button", "btn ghost", label);
    a.onclick = () => call("act", "open_url", { url });
    wrap.appendChild(a);
  }
  const dirs = el("div", "btns");
  for (const [label, which] of [["打开程序目录", "root"], ["打开配置目录", "config"]]) {
    const b = el("button", "btn ghost", label);
    b.onclick = () => call("act", "openDir", { which });
    dirs.appendChild(b);
  }
  s3.appendChild(line("目录", dirs));
  s3.appendChild(line("", wrap));
  box.appendChild(s3);
  const s5 = section("隐私与捐赠");
  s5.appendChild(line("条款", el("div", "note",
    "所有数据只在自己的电脑与手机之间经 USB 传输，不收集、不上传任何东西。")));
  const b5 = el("div", "btns");
  const pv = el("button", "btn ghost", "隐私政策与使用条款");
  pv.onclick = () => openDoc("privacy");
  const dn = el("button", "btn ghost", "请作者喝杯奶茶");
  dn.onclick = () => openDonate();
  b5.append(pv, dn);
  s5.appendChild(line("", b5));
  box.appendChild(s5);
  const s4 = section("维护");
  s4.appendChild(line("", el("div", "note",
    "重置会把画面、编码、启动这些参数恢复成默认值，并删掉已保存的设置文件。")));
  const b4 = el("div", "btns");
  const reset = el("button", "btn ghost", "重置设置");
  reset.onclick = () => call("act", "resetSettings");
  b4.appendChild(reset);
  s4.appendChild(line("", b4));
  box.appendChild(s4);
}

/* -------------------------------------------------------------------- boot */
function bindInputs() {
  for (const id of ["inpW", "inpH"]) {
    const e = document.getElementById(id);
    if (!e) continue;
    e.addEventListener("change", () => {
      const key = id === "inpW" ? "w" : "h";
      call("act", "set", { field: key, value: e.value });
    });
  }
}
document.addEventListener("mousedown", (e) => {
  if (!e.target.closest("#pop") && !e.target.closest(".sel")) closePop();
});

window.addEventListener("pywebviewready", () => {
  showView("main");
  askTermsIfNeeded().catch(() => {});
  bindWindow();
  bindSelects();
  $("#btnBack").onclick = () => showView("main");
  $("#btnDone").onclick = () => showView("main");
  $("#btnStart").onclick = () => call("act", "start");
  $("#btnStop").onclick = () => call("act", "stop");
  for (const b of $$(".lact")) {
    b.onclick = () => {
      const a = b.dataset.act;
      if (a === "clearLog") { $("#logBody").innerHTML = ""; return; }
      if (a === "copyLog") {
        const text = $("#logBody").innerText;
        navigator.clipboard.writeText(text);
        return;
      }
      if (a === "saveLog") { call("act", "saveLog"); return; }
      const lg = $(".logwrap");
      const on = lg.classList.toggle("expanded");
      b.textContent = on ? "收起 ▴" : "展开 ▾";
      return;
    };
  }
  pump();
  setInterval(pump, 400);
});
/* First run: the terms, drawn in the page instead of a Tk window.  This is the
   one dialog nobody can click past -- agreeing is what lets the program run. */
async function askTermsIfNeeded() {
  const snap = await call("snapshot");
  if (!snap || !snap.terms) return;
  const d = await call("act", "privacy");
  const wrap = el("div", "modal");
  const box = el("div", "box big");
  box.appendChild(el("div", "ttl", (d && d.title) || "使用条款与隐私说明"));
  box.appendChild(el("div", "msg", "第一次使用前请读一遍。同意后不会再问。"));
  const body = el("div", "docbody");
  body.textContent = (d && d.body) || "";
  box.appendChild(body);
  const acts = el("div", "acts");
  const no = el("button", "btn ghost", "不同意，退出");
  no.onclick = () => call("act", "quit");
  const yes = el("button", "btn primary", "同意并继续");
  yes.onclick = async () => {
    yes.disabled = true;
    await call("act", "acceptTerms");
    wrap.remove();
  };
  acts.append(no, yes);
  box.appendChild(acts);
  wrap.appendChild(box);
  document.body.appendChild(wrap);
}

/* The X. "Ask" is the default, and the answer is remembered on request. */
function askClose() {
  const wrap = el("div", "modal");
  const box = el("div", "box");
  box.appendChild(el("div", "ttl", "关闭 DroidExtend"));
  box.appendChild(el("div", "msg", "要把窗口收进托盘（投屏继续），还是直接退出？"));
  const opt = el("label", "chk opt");
  const cb = document.createElement("input");
  cb.type = "checkbox";
  opt.appendChild(cb);
  opt.appendChild(el("span", null, "记住这个选择"));
  box.appendChild(opt);
  const acts = el("div", "acts");
  const done = (how) => {
    wrap.remove();
    if (cb.checked) call("act", "set", { field: "closeAction", value: how === "quit" ? "quit" : "tray" });
    if (how === "quit") call("act", "quit");
    else call("win", "min");
  };
  const cancel = el("button", "btn ghost", "取消");
  cancel.onclick = () => wrap.remove();
  const tray = el("button", "btn ghost", "收进托盘");
  tray.onclick = () => done("tray");
  const quit = el("button", "btn primary", "退出");
  quit.onclick = () => done("quit");
  acts.appendChild(cancel); acts.appendChild(tray); acts.appendChild(quit);
  box.appendChild(acts);
  wrap.appendChild(box);
  wrap.onmousedown = (e) => { if (e.target === wrap) wrap.remove(); };
  document.body.appendChild(wrap);
}

/* Long text -- manual, changelog, about, licences -- in a reader of its own.
   The Tk build opened a Toplevel with a Text widget for each; the page draws
   the same words here, in the same grey. */
async function openDoc(which) {
  const d = which === "privacy" ? await call("act", "privacy")
    : await call("act", "doc", { which });
  if (!d || !d.body) return;
  const wrap = el("div", "modal");
  const box = el("div", "box big");
  const head = el("div", "dh");
  head.appendChild(el("div", "ttl", d.title || ""));
  const x = el("button", "wbtn close", "✕");
  x.onclick = () => wrap.remove();
  head.appendChild(x);
  box.appendChild(head);
  const body = el("div", "docbody");
  body.textContent = d.body;
  box.appendChild(body);
  const acts = el("div", "acts");
  for (const [label, url] of (d.links || [])) {
    const b = el("button", "btn ghost", label);
    b.onclick = () => call("act", "open_url", { url });
    acts.appendChild(b);
  }
  const close = el("button", "btn primary", "关闭");
  close.onclick = () => wrap.remove();
  acts.appendChild(close);
  box.appendChild(acts);
  wrap.appendChild(box);
  wrap.onmousedown = (e) => { if (e.target === wrap) wrap.remove(); };
  document.body.appendChild(wrap);
}

/* The one place the program asks for money -- a tab of text and two QR codes,
   the same offer the Tk build put behind Help.  Nothing here is on the way of
   the stream. */
function openDonate() {
  const wrap = el("div", "modal");
  const box = el("div", "box big");
  const head = el("div", "dh");
  head.appendChild(el("div", "ttl", "请作者喝杯奶茶"));
  const x = el("button", "wbtn close", "✕");
  x.onclick = () => wrap.remove();
  head.appendChild(x);
  box.appendChild(head);
  const msg = el("div", "msg");
  msg.textContent = "DroidExtend 免费、没有广告、没有内购、没有功能锁，代码全在 GitHub 上。"
    + "如果它帮你省下了一块便携屏，或者只是觉得好用，可以请作者喝一杯奶茶，"
    + "或者资助一点 token —— 这个项目确实是 AI 写的，token 就是它的电费。"
    + "不捐也完全没问题，功能一个都不会少。";
  box.appendChild(msg);
  const row = el("div", "qr");
  for (const [file, label] of [["assets/donate-wechat.png", "微信支付"],
                               ["assets/donate-alipay.png", "支付宝"]]) {
    const cell = el("div", "qrc");
    const img = el("img");
    img.src = file;
    img.alt = label;
    cell.appendChild(img);
    cell.appendChild(el("div", "qrcap", label));
    row.appendChild(cell);
  }
  box.appendChild(row);
  const acts = el("div", "acts");
  const ok = el("button", "btn primary", "关闭");
  ok.onclick = () => wrap.remove();
  acts.appendChild(ok);
  box.appendChild(acts);
  wrap.appendChild(box);
  wrap.onmousedown = (e) => { if (e.target === wrap) wrap.remove(); };
  document.body.appendChild(wrap);
}

/* The program wants to replace the app on the phone and needs a yes.  The
   thread that asked is waiting on this answer, so the box has no close button:
   both buttons answer, and the timeout in Python eventually answers for us. */
let askSeen = 0;
function offerAsk(a) {
  if (!a || !a.id || a.id === askSeen) return;
  if (!document.getElementById("btnStart")) return;
  askSeen = a.id;
  const p = a.payload || {};
  const wrap = el("div", "modal");
  const box = el("div", "box big");
  box.appendChild(el("div", "ttl", "手机端 App 需要更新"));
  const msg = el("div", "msg");
  msg.textContent = "手机上是 " + (p.old || "旧版本") + "，程序里带的是 "
    + (p.name || p.new || "新版本") + "。更新会先卸载旧版本再装，手机上的偏好设置不会丢。";
  box.appendChild(msg);
  const acts = el("div", "acts");
  const no = el("button", "btn ghost", "这次不更新");
  no.onclick = () => {
    wrap.remove();
    call("act", "answer", { id: a.id, ok: false });
  };
  const yes = el("button", "btn primary", "更新");
  yes.onclick = () => {
    wrap.remove();
    call("act", "answer", { id: a.id, ok: true });
  };
  acts.append(no, yes);
  box.appendChild(acts);
  wrap.appendChild(box);
  document.body.appendChild(wrap);
}

/* A newer release exists: the Tk build popped a box, so does this one. Once per
   version per run -- the log keeps the rest of the story. */
let updSeen = "";
function offerUpdate(u) {
  if (!u || !u.latest || u.latest === updSeen) return;
  if (!document.getElementById("btnStart")) return;
  updSeen = u.latest;
  const wrap = el("div", "modal");
  const box = el("div", "box big");
  box.appendChild(el("div", "ttl", "有新版本 v" + u.latest + (u.pre ? "（测试版）" : "")));
  const msg = el("div", "msg");
  msg.textContent = "当前是 v" + ((S.snap && S.snap.version) || "") + "。";
  box.appendChild(msg);
  if (u.notes) {
    const body = el("div", "docbody");
    body.textContent = u.notes;
    box.appendChild(body);
  }
  const acts = el("div", "acts");
  const later = el("button", "btn ghost", "以后再说");
  later.onclick = () => wrap.remove();
  acts.appendChild(later);
  const go = el("button", "btn primary", "打开下载页");
  go.onclick = () => { call("act", "open_url", { url: u.url }); wrap.remove(); };
  acts.appendChild(go);
  box.appendChild(acts);
  wrap.appendChild(box);
  wrap.onmousedown = (e) => { if (e.target === wrap) wrap.remove(); };
  document.body.appendChild(wrap);
}
