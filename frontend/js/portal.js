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
      fetch('/api/audit/knowledge/violations?per_page=1').then(function(r){return r.json();}).catch(function(){return {total:0};})
    ]).then(function(results){
      var projects = results[0].projects || [];
      var tasks = results[1];
      var violations = results[2];
      var stats = {
        projects: projects.length || 0,
        tasks: tasks.total || 0,
        completed: tasks.completed || 0,
        cases: violations.total || 0
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
      countUp(document.getElementById('stat-cases'),   0, 1400);
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

  // ---- Load todos ----（P3-6: 从 API 加载真实待办 + 后台任务）
  function loadTodos() {
    const list = document.getElementById('todo-list');
    if (!list) return;
    var pm = AuditWorkbench.getProjectMemory();
    var projName = (pm && pm.title) ? pm.title : '';

    var todos = [];
    // 督办提醒（项目上下文）
    if(projName) {
      todos.push({ id:'supervise', text:'📋 '+projName+' · 审计通知书尚未上传', deadline:'立办', priority:'high', isSupervision:true });
    }
    // 从后台任务系统取真实待办
    if (typeof AuditAPI !== 'undefined' && AuditAPI.tasks) {
      AuditAPI.tasks.list({status:'processing', limit:5}).then(function(resp) {
        if (resp && resp.success && resp.tasks) {
          resp.tasks.forEach(function(t) {
            todos.push({
              id: 'task-' + t.id,
              text: (t.task_type==='ocr'?'📄 ':'🤖 ') + t.task_name + ' (' + t.progress + '%)',
              deadline: '后台处理中', priority: 'medium', isBgTask: true
            });
          });
        }
        _renderTodos(todos);
      }).catch(function() { _renderTodos(todos); });
    } else {
      _renderTodos(todos);
    }

    function _renderTodos(todos) {
      var cnt = document.getElementById('todo-count');
      if (cnt) cnt.textContent = todos.length;
      if (todos.length === 0) {
        list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--color-text-muted);">暂无待办事项</div>';
        return;
      }
      list.innerHTML = todos.map(function(t) {
        return '<div class="todo-item" style="' + (t.isSupervision?'background:rgba(184,94,26,0.06);border-radius:var(--radius-sm);padding:6px 8px;':t.isBgTask?'background:rgba(184,94,26,0.05);border-radius:var(--radius-sm);':'') + '">' +
          '<div class="todo-priority ' + t.priority + '"></div>' +
          '<label style="flex:1;font-size:14px;' + (t.isSupervision?'font-weight:500;':'') + '">' + t.text + '</label>' +
          '<span class="todo-deadline" style="' + (t.isSupervision?'color:var(--color-accent);font-weight:600;':'') + '">' + t.deadline + '</span>' +
          (t.isBgTask ? '<span class="pulse" style="color:var(--color-warning);font-size:12px;">●</span>' : '') +
        '</div>';
      }).join('');
    }
  }

  // ---- Load document status ----
  function loadDocStatus() {
    // 任务真相在后端 audit_task_queue；拉取后统计 OCR 文档处理状态
    const list = document.getElementById('doc-processing-list');
    const emptyHtml = '<div style="padding:14px;font-size:13px;color:var(--color-text-muted);text-align:center;">暂无处理中文档</div>';
    if (typeof AuditAPI === 'undefined' || !AuditAPI.tasks) {
      if (list) list.innerHTML = emptyHtml;
      return;
    }
    AuditAPI.tasks.list({ limit: 100 }).then(function(resp) {
      const tasks = (resp && resp.success && resp.tasks) ? resp.tasks : [];
      const ocrTasks = tasks.filter(t => (t.task_type || t.type) === 'ocr');
      const processing = ocrTasks.filter(t => t.status === 'processing' || t.status === 'pending').length;
      const completed = ocrTasks.filter(t => t.status === 'completed').length;
      const total = ocrTasks.length;
      const pending = Math.max(0, total - completed - processing);

      countUp(document.getElementById('stat-total-docs'), total, 600);
      countUp(document.getElementById('stat-parsed-docs'), completed, 800);
      countUp(document.getElementById('stat-processing-docs'), processing, 1000);
      countUp(document.getElementById('stat-pending-docs'), pending, 1200);

      // Show processing items（无则空状态，不塞假项）
      if (!list) return;
      const procItems = ocrTasks.filter(t => t.status === 'processing' || t.status === 'pending');
      if (!procItems.length) { list.innerHTML = emptyHtml; return; }
      list.innerHTML = procItems.map(item => {
        const prog = (typeof item.progress === 'number') ? item.progress : 65;
        return `
          <div style="display:flex;align-items:center;gap:8px;padding:6px 0;font-size:13px;">
            <span class="pulse" style="color:var(--color-warning);">●</span>
            <span>${item.task_name || item.name || '文档'}</span>
            <span style="color:var(--color-text-muted);">识别中...</span>
            <div class="progress" style="width:100px;margin-left:auto;"><div class="progress-bar" style="width:${Math.max(15,prog)}%;"></div></div>
            <a href="docworkshop.html" style="font-size:12px;">查看</a>
          </div>`;
      }).join('');
    }).catch(function() {
      if (list) list.innerHTML = emptyHtml;
    });
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
      '<div style="padding:20px;" id="search-results"><p style="text-align:center;color:var(--color-text-muted);">正在检索法规库 + 违规模型库...</p></div></div>';
    modal.addEventListener('click', function(e) { if (e.target === this) this.remove(); });
    document.body.appendChild(modal);

    // P3-6: 并行调真实 API
    var html = '';
    var pending = 2;
    function tryRender() { if (--pending === 0 && html) document.getElementById('search-results').innerHTML = html; }

    // 违规模型
    AuditAPI.knowledge.violations({q: q, per_page: 3}).then(function(resp) {
      if (resp.success && resp.violations && resp.violations.length > 0) {
        html += '<h4 style="color:var(--color-primary);margin-bottom:8px;"><i class="bi bi-exclamation-triangle"></i> 违规模型 (' + resp.total + '条匹配)</h4>';
        resp.violations.forEach(function(v) {
          html += '<div class="rec-item" style="cursor:pointer;" onclick="location.href=\'knowledge.html\'"><div style="flex:1;"><strong>' + (v.violation_title || '') + '</strong><div style="font-size:12px;color:var(--color-text-muted);">' + (v.description || '').substring(0, 60) + '</div></div><span class="badge badge-' + (v.severity === 'high' ? 'accent' : 'primary') + '">' + (v.severity || '') + '</span></div>';
        });
      }
      tryRender();
    }).catch(function() { tryRender(); });

    // 法规
    AuditAPI.knowledge.regulations({q: q, per_page: 3}).then(function(resp) {
      if (resp.success && resp.regulations && resp.regulations.length > 0) {
        html = '<h4 style="color:var(--color-primary);margin-bottom:8px;"><i class="bi bi-journal-text"></i> 法规依据 (' + resp.total + '条匹配)</h4>' +
          resp.regulations.map(function(l) {
            return '<div class="rec-item" style="cursor:pointer;" onclick="location.href=\'knowledge.html\'"><div style="flex:1;"><strong>' + (l.title || '') + '</strong><div style="font-size:12px;color:var(--color-text-muted);">' + (l.potency_level || '') + ' · ' + (l.timeliness || '') + '</div></div></div>';
          }).join('') + html;
      }
      tryRender();
    }).catch(function() { tryRender(); });

    // 语义搜索（FAISS）
    if (AuditAPI.search) {
      AuditAPI.search.laws(q, 2).then(function(resp) {
        if (resp.success && resp.results && resp.results.length > 0) {
          html += '<h4 style="color:var(--color-primary);margin-top:12px;"><i class="bi bi-stars"></i> 语义匹配法规</h4>';
          resp.results.forEach(function(l) {
            html += '<div class="rec-item"><div style="flex:1;"><strong>' + (l.title || '') + '</strong><div style="font-size:12px;color:var(--color-text-muted);">相似度: ' + ((l.similarity || 0) * 100).toFixed(0) + '%</div></div></div>';
          });
          try { document.getElementById('search-results').innerHTML = html || '<p style="color:var(--color-text-muted);">未找到匹配结果</p>'; } catch(e) {}
        }
      }).catch(function() {});
    }
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
