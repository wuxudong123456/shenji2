/**
 * AuditWorkbench — 门户首页交互
 */
// 侧边栏折叠（所有页面共用）
document.addEventListener('DOMContentLoaded',function(){
  var btn = document.getElementById('sidebar-toggle-btn');
  if(!btn) return;
  btn.onclick = function(){
    var s = document.getElementById('aw-sidebar');
    var c = document.querySelector('.app-content');
    var cl = s.classList.toggle('collapsed');
    if(c) c.classList.toggle('expanded');
    this.style.left = cl ? '52px' : '216px';
    this.innerHTML = cl ? '<i class="bi bi-chevron-right"></i>' : '<i class="bi bi-chevron-left"></i>';
    localStorage.setItem('aw_sidebar_collapsed', cl ? '1' : '0');
  };
});

var Portal = window.Portal = {
  docStatusVisible: true,

  toggleDocStatus: function() {
    this.docStatusVisible = !this.docStatusVisible;
    var card = document.getElementById('doc-status-card');
    var body = document.getElementById('doc-status-body');
    var mini = document.getElementById('doc-status-mini');
    var btn  = document.getElementById('doc-status-show-btn');
    var icon = document.getElementById('doc-status-toggle-icon');

    if (this.docStatusVisible) {
      if (card) card.style.display = '';
      if (body) body.style.display = '';
      if (mini) mini.style.display = 'none';
      if (btn)  btn.style.display = 'none';
      if (icon) icon.className = 'bi bi-chevron-up';
    } else {
      if (card) card.style.display = 'none';
      if (body) body.style.display = 'none';
      if (mini) {
        mini.style.display = 'flex';
        var pEl = document.getElementById('stat-processing-docs');
        var cEl = document.getElementById('stat-parsed-docs');
        var mpEl = document.getElementById('mini-processing');
        var mcEl = document.getElementById('mini-completed');
        if (mpEl && pEl) mpEl.textContent = (parseInt(pEl.textContent) || 0);
        if (mcEl && cEl) mcEl.textContent = (parseInt(cEl.textContent) || 0);
      }
      if (btn)  btn.style.display = 'block';
      if (icon) icon.className = 'bi bi-chevron-down';
    }
    localStorage.setItem('aw_doc_status_visible', this.docStatusVisible ? 'true' : 'false');
  }
};

(function() {
  'use strict';

  // ---- Greeting & Date ----
  function updateGreeting() {
    const now = new Date();
    const hour = now.getHours();
    const greeting = hour < 12 ? '早上好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好';
    const el = document.getElementById('greeting');
    if (el) el.textContent = greeting;

    const dateEl = document.getElementById('today-date');
    if (dateEl) dateEl.textContent = now.toLocaleDateString('zh-CN', { year:'numeric', month:'long', day:'numeric' });

    const wdEl = document.getElementById('today-weekday');
    if (wdEl) wdEl.textContent = '星期' + ['日','一','二','三','四','五','六'][now.getDay()];
  }

  // ---- Animate counter ----
  function countUp(el, target, duration) {
    if (!el) return;
    const start = 0;
    const startTime = performance.now();
    function tick(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // ease-out
      el.textContent = Math.floor(start + (target - start) * eased).toLocaleString();
      if (progress < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  // ---- Load stats ----
  function loadStats() {
    // 从真实API加载统计数据，失败时降级为默认值
    Promise.all([
      fetch('/api/audit/projects').then(function(r){return r.json();}).catch(function(){return {projects:[]};}),
      fetch('/api/audit/tasks?limit=1').then(function(r){return r.json();}).catch(function(){return {total:0,completed:0};}),
      fetch('/api/audit/knowledge/violations?per_page=1').then(function(r){return r.json();}).catch(function(){return {total:2231};})
    ]).then(function(results){
      var projects = results[0].projects || [];
      var tasks = results[1];
      var violations = results[2];
      var stats = {
        projects: projects.length || 0,
        tasks: tasks.total || 0,
        completed: tasks.completed || 0,
        cases: violations.total || 2231
      };
      countUp(document.getElementById('stat-projects'), stats.projects, 800);
      countUp(document.getElementById('stat-tasks'),    stats.tasks,    1000);
      countUp(document.getElementById('stat-completed'),stats.completed,1200);
      countUp(document.getElementById('stat-cases'),   stats.cases,    1400);
    }).catch(function(){
      // 降级: API完全不可用时
      countUp(document.getElementById('stat-projects'), 0, 800);
      countUp(document.getElementById('stat-tasks'),    0, 1000);
      countUp(document.getElementById('stat-completed'),0, 1200);
      countUp(document.getElementById('stat-cases'),   2231, 1400);
    });
  }

  // ---- Load activities ----
  function loadActivities() {
    const list = document.getElementById('activity-list');
    if (!list) return;

    // 从真实 API 加载近期项目作为活动列表
    fetch('/api/audit/projects').then(function(r){return r.json();}).then(function(data){
      var projects = (data.projects || []).slice(0, 5);
      if (projects.length === 0) {
        list.innerHTML = '<div class="activity-item"><span class="activity-time">—</span><span class="activity-text">暂无活动，创建第一个审计项目开始</span></div>';
        return;
      }
      list.innerHTML = projects.map(function(p) {
        var created = p.create_time ? new Date(p.create_time) : new Date();
        var timeStr = created.toLocaleDateString('zh-CN');
        return '<div class="activity-item">' +
          '<span class="activity-time">' + timeStr + '</span>' +
          '<span class="activity-text">项目「' + (p.name || '未命名') + '」' + (p.status === 'completed' ? '已完成' : '进行中') + '</span>' +
        '</div>';
      }).join('');
    }).catch(function(){
      list.innerHTML = '<div class="activity-item"><span class="activity-text">无法加载活动数据</span></div>';
    });
  }

  // ---- Load todos ----
  function loadTodos() {
    const list = document.getElementById('todo-list');
    if (!list) return;
    // 读取当前项目
    var projMem = localStorage.getItem('aw_project_memory');
    var projName = '';
    try { var pm = JSON.parse(projMem); if(pm && pm.title) projName = pm.title; } catch(e) {}
    var todos = [
      { id:1, text:'复核Q2财务审计报告', deadline:'2026-07-05', priority:'high' },
      { id:2, text:'提交内控评估报告', deadline:'2026-07-06', priority:'high' },
      { id:3, text:'更新审计程序模板', deadline:'2026-07-08', priority:'medium' },
    ];
    // 督办提醒
    if(projName) {
      todos.unshift({
        id:'supervise', text:'📋 '+projName+' · 审计通知书尚未上传，出具报告阶段资料空缺',
        deadline:'立办', priority:'high', isSupervision:true
      });
    }
    // Add background tasks from OCR queue
    const bgTasks = AuditWorkbench.backgroundTasks.filter(t => t.status === 'processing');
    bgTasks.forEach((t, i) => {
      todos.push({
        id: 'bg-' + t.id,
        text: (t.type==='ocr'?'📄 ':'🤖 ') + t.name + (t.type==='ocr'?' (OCR解析中)':' (分析中)'),
        deadline: '后台处理中',
        priority: 'medium',
        isBgTask: true
      });
    });

    document.getElementById('todo-count').textContent = todos.length;
    list.innerHTML = todos.map(t => `
      <div class="todo-item" style="${t.isSupervision?'background:rgba(184,94,26,0.06);border-radius:var(--radius-sm);padding:6px 8px;':t.isBgTask?'background:rgba(184,94,26,0.05);border-radius:var(--radius-sm);':''}">
        <div class="todo-priority ${t.priority}"></div>
        <input type="checkbox" class="todo-check" id="todo-${t.id}" ${t.isBgTask||t.isSupervision?'':'onchange="this.parentElement.style.opacity=this.checked?\'0.5\':\'1\'"'}>
        <label for="todo-${t.id}" style="flex:1;font-size:14px;${t.isSupervision?'font-weight:500;':''}">${t.text}</label>
        <span class="todo-deadline" style="${t.isSupervision?'color:var(--color-accent);font-weight:600;':''}">${t.deadline}</span>
        ${t.isSupervision ? '<a href="projects.html" style="font-size:11px;color:var(--color-primary);margin-left:4px;">查看详情 →</a>' : ''}
        ${t.isBgTask ? '<span class="pulse" style="color:var(--color-warning);font-size:12px;">●</span>' : ''}
      </div>
    `).join('');
  }

  // ---- Load document status ----
  function loadDocStatus() {
    // From background tasks + mock data
    const tasks = AuditWorkbench.backgroundTasks;
    const processing = tasks.filter(t => t.status === 'processing' && t.type === 'ocr').length;
    const total = 6;  // Mock total
    const parsed = total - processing - 1;
    const pending = 1;

    countUp(document.getElementById('stat-total-docs'), total, 600);
    countUp(document.getElementById('stat-parsed-docs'), parsed, 800);
    countUp(document.getElementById('stat-processing-docs'), processing, 1000);
    countUp(document.getElementById('stat-pending-docs'), pending, 1200);

    // Show processing items
    const list = document.getElementById('doc-processing-list');
    if (!list) return;

    const items = [];
    tasks.filter(t => t.status === 'processing').forEach(t => {
      items.push({ name: t.name, status: 'processing', type: 'ocr' });
    });
    // Add mock items if empty
    if (items.length === 0) {
      items.push({ name: '银行流水2026Q1.csv', status: 'processing', type: 'ocr' });
    }

    list.innerHTML = items.map(item => `
      <div style="display:flex;align-items:center;gap:8px;padding:6px 0;font-size:13px;">
        <span class="pulse" style="color:var(--color-warning);">●</span>
        <span>${item.name}</span>
        <span style="color:var(--color-text-muted);">识别中...</span>
        <div class="progress" style="width:100px;margin-left:auto;"><div class="progress-bar" style="width:65%;"></div></div>
        <a href="docworkshop.html" style="font-size:12px;">查看</a>
      </div>
    `).join('');
  }

// ---- Smart Search ----
var SmartSearch = window.SmartSearch = {
  query: function() {
    var q = document.getElementById('smart-search-input').value.trim();
    if (!q) return AuditWorkbench.toast('请输入检索内容', 'warning');
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:flex-start;justify-content:center;padding-top:80px;';
    modal.innerHTML = '<div style="background:#fff;border-radius:var(--radius-lg);max-width:800px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:var(--shadow-lg);"><div style="padding:16px 20px;border-bottom:2px solid var(--color-border);display:flex;align-items:center;gap:8px;">' +
      '<i class="bi bi-search" style="font-size:20px;color:var(--color-primary);"></i>' +
      '<strong style="font-size:16px;">智能检索: ' + q + '</strong>' +
      '<span class="pulse" style="color:var(--color-warning);margin-left:8px;font-size:12px;">● 检索中...</span>' +
      '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="margin-left:auto;background:none;border:none;font-size:20px;cursor:pointer;">&times;</button></div>' +
      '<div style="padding:20px;" id="search-results"><p style="text-align:center;color:var(--color-text-muted);">正在检索 400,698部法规 + 2,195违规模型 + 2,231案例...</p></div></div>';
    modal.addEventListener('click', function(e) { if (e.target === this) this.remove(); });
    document.body.appendChild(modal);

    setTimeout(function() {
      var container = document.getElementById('search-results');
      if (!container) return;
      container.innerHTML =
        '<h4 style="color:var(--color-primary);margin-bottom:8px;"><i class="bi bi-exclamation-triangle"></i> 违规模型 (2条匹配)</h4>' +
        '<div class="rec-item" style="cursor:pointer;" onclick="location.href=\'knowledge.html#violations\'"><div style="flex:1;"><strong>化整为零规避公开招标</strong><div style="font-size:12px;color:var(--color-text-muted);">将应公开招标项目拆分为多个小额项目 · 匹配度97%</div></div><span class="badge badge-primary">部门预算执行</span></div>' +
        '<div class="rec-item" style="cursor:pointer;"><div style="flex:1;"><strong>违规采用询价方式采购</strong><div style="font-size:12px;color:var(--color-text-muted);">达到门槛的项目采用非公开方式 · 匹配度89%</div></div><span class="badge badge-primary">部门预算执行</span></div>' +
        '<h4 style="color:var(--color-primary);margin-top:12px;margin-bottom:8px;"><i class="bi bi-journal-text"></i> 法规依据 (3条匹配)</h4>' +
        '<div class="rec-item" style="cursor:pointer;" onclick="location.href=\'knowledge.html#regulations\'"><div style="flex:1;"><strong>《招标投标法》第4条</strong><div style="font-size:12px;color:var(--color-text-muted);">禁止化整为零或以其他方式规避招标 · 法律 · 现行有效</div></div><a class="trace-link" href="#">溯源</a></div>' +
        '<div class="rec-item" style="cursor:pointer;"><div style="flex:1;"><strong>《必须招标的工程项目规定》第5条</strong><div style="font-size:12px;color:var(--color-text-muted);">货物采购≥200万须公开招标 · 部门规章 · 现行有效</div></div><a class="trace-link" href="#">溯源</a></div>' +
        '<div class="rec-item" style="cursor:pointer;"><div style="flex:1;"><strong>《政府采购法》第28条</strong><div style="font-size:12px;color:var(--color-text-muted);">公开招标应为主要采购方式 · 法律 · 现行有效</div></div><a class="trace-link" href="#">溯源</a></div>' +
        '<h4 style="color:var(--color-primary);margin-top:12px;margin-bottom:8px;"><i class="bi bi-files"></i> 相关案例 (2条匹配)</h4>' +
        '<div class="rec-item" style="cursor:pointer;" onclick="location.href=\'knowledge.html#cases\'"><div style="flex:1;"><strong>某市教育局教学设备采购规避招标案</strong><div style="font-size:12px;color:var(--color-text-muted);">拆分为5个99万子项目 · ¥4,850,000</div></div><span class="badge badge-accent">同类</span></div>' +
        '<div class="rec-item" style="cursor:pointer;"><div style="flex:1;"><strong>某市卫健委医疗设备采购拆分案</strong><div style="font-size:12px;color:var(--color-text-muted);">拆分为4个80万子项目 · ¥3,200,000</div></div><span class="badge badge-accent">同类</span></div>' +
        '<h4 style="color:var(--color-primary);margin-top:12px;margin-bottom:8px;"><i class="bi bi-robot"></i> LLM智能回答</h4>' +
        '<div class="alert alert-info" style="margin-bottom:0;">根据检索结果，<strong>化整为零规避公开招标</strong>是您关注的核心问题。《招标投标法》第4条明确规定禁止此类行为。处罚依据为第49条，处合同金额5‰-10‰罚款。<br><span style="font-size:12px;color:var(--color-text-muted);">引用法规: 3部 | 违规模型: 2个 | 案例: 2个</span></div>' +
        '<div style="margin-top:12px;display:flex;gap:8px;"><button class="btn btn-primary btn-sm" onclick="location.href=\'lawqa.html\'"><i class="bi bi-chat-dots"></i> 深入对话</button>' +
        '<button class="btn btn-outline btn-sm" onclick="location.href=\'analysis.html\'"><i class="bi bi-play-fill"></i> 启动智能分析</button></div>';
    }, 1200);
  },

  quick: function(q) {
    document.getElementById('smart-search-input').value = q;
    this.query();
  }
};

  // ---- Init ----
  document.addEventListener('DOMContentLoaded', () => {
    updateGreeting();
    setTimeout(loadStats, 300);
    setTimeout(loadDocStatus, 400);
    loadActivities();
    loadTodos();
    // Restore collapsed state after DOM ready
    setTimeout(function() {
      if (localStorage.getItem('aw_doc_status_visible') === 'false') {
        Portal.docStatusVisible = true;
        Portal.toggleDocStatus();
      }
    }, 500);
  });
})();
