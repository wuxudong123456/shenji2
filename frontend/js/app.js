/**
 * AuditWorkbench — Global Navigation Framework
 */
const AuditWorkbench = {
  version: '1.0.0',
  currentPage: '',

  nav: [
    { id: 'home', icon: 'bi-speedometer2', label: '首页', href: 'index.html' },
    { id: 'projects', icon: 'bi-folder2-open', label: '审计项目', href: 'projects.html',
      children: [{ id: 'analysis', icon: 'bi-cpu', label: '智能分析', href: 'analysis.html' }] },
    { type: 'divider', label: '三大工坊' },
    { id: 'knowledge', icon: 'bi-book', label: '知识工坊', href: 'knowledge.html',
      children: [
        { id: 'kw-violations', icon: 'bi-exclamation-triangle', label: '违规行为库', href: 'knowledge.html#violations' },
        { id: 'kw-regulations', icon: 'bi-journal-text', label: '审计依据库', href: 'knowledge.html#regulations' },
        { id: 'kw-cases', icon: 'bi-files', label: '审计案例库', href: 'knowledge.html#cases' }
      ] },
    { id: 'dataworkshop', icon: 'bi-grid-3x3-gap', label: '数据工坊', href: 'dataworkshop.html',
      children: [
        { id: 'dw-tables', icon: 'bi-table', label: '结构化数据表', href: 'dataworkshop.html#tables' },
        { id: 'dw-query', icon: 'bi-magic', label: '智能问数', href: 'dataworkshop.html#query' }
      ] },
    { id: 'docworkshop', icon: 'bi-file-earmark-text', label: '资料工坊', href: 'docworkshop.html',
      children: [
        { id: 'dw-docs', icon: 'bi-folder', label: '我的文档', href: 'docworkshop.html#docs' },
        { id: 'dw-templates', icon: 'bi-file-earmark-code', label: '提取模板', href: 'docworkshop.html#templates' },
        { id: 'dw-reinfer', icon: 'bi-arrow-repeat', label: '重新推理', href: 'docworkshop.html#reinfer' }
      ] },
    { type: 'divider', label: '智能工具' },
    { id: 'lawqa', icon: 'bi-chat-dots', label: '法规问答', href: 'lawqa.html' },
    { id: 'qualification', icon: 'bi-crosshair', label: '审计定性', href: 'qualification.html' },
    { id: 'documents', icon: 'bi-file-earmark-pdf', label: '文书生成', href: 'documents.html' },
    { id: 'review', icon: 'bi-check2-all', label: '审理复核', href: 'review.html' },
    { id: 'toolbox', icon: 'bi-tools', label: '工具箱', href: 'toolbox.html' },
    { type: 'divider', label: '配置' },
    { id: 'workspace', icon: 'bi-person', label: '我的空间', href: 'workspace.html' }
  ],

  init(currentPageId) {
    this.currentPage = currentPageId;
    this.renderNavbar();
    this.renderSidebar();
    this.bindEvents();
    // 任务状态以后端 audit_task_queue 为唯一真相，启动时拉取一次徽章
    this.updateTaskBadge();
    // 方案B：每次进页面异步回查项目记忆，自愈 stale 缓存（无 pid 则跳过）
    this.refreshProjectMemory();
  },

  renderNavbar() {
    var n = document.getElementById('aw-navbar');
    if (!n) return;
    n.innerHTML = '<a class="app-navbar-brand" href="index.html">' +
      '<span class="brand-icon" style="width:36px;height:36px;font-size:18px;"><i class="bi bi-briefcase-fill"></i></span>' +
      '<span style="display:flex;flex-direction:column;line-height:1.2;">' +
      '<span style="font-size:18px;font-weight:700;">审计实务工坊</span>' +
      '<span style="font-size:11px;opacity:0.5;font-weight:400;">AuditWorkbench v' + this.version + '</span>' +
      '</span></a>' +
      '<div class="app-navbar-actions">' +
      '<span id="nav-task-badge" style="position:relative;cursor:pointer;" onclick="AuditWorkbench.showTasks()" title="后台任务">' +
      '<i class="bi bi-hourglass-split" style="color:rgba(255,255,255,0.8);font-size:18px;"></i>' +
      '<span id="nav-task-count" style="display:none;position:absolute;top:-6px;right:-8px;background:var(--color-accent);color:#fff;font-size:10px;width:18px;height:18px;border-radius:50%;text-align:center;line-height:18px;font-weight:700;">0</span>' +
      '</span>' +
      '<button class="btn btn-sm" style="background:rgba(255,255,255,0.1);color:#fff;border:none;" onclick="AuditWorkbench.showGuide()">' +
      '<i class="bi bi-question-circle"></i></button>' +
      '<div class="user-dropdown" style="position:relative;">' +
      '<button class="btn btn-sm" style="background:rgba(255,255,255,0.1);color:#fff;border:none;display:flex;align-items:center;gap:6px;" onclick="AuditWorkbench.toggleUserMenu(event)">' +
      '<i class="bi bi-person-circle"></i> 审计员 <i class="bi bi-chevron-down" style="font-size:10px;"></i></button>' +
      '<div class="user-menu" id="user-menu" style="display:none;position:absolute;top:100%;right:0;margin-top:6px;background:#fff;border-radius:var(--radius-md);box-shadow:var(--shadow-lg);min-width:200px;z-index:9999;overflow:hidden;">' +
      '<div style="padding:12px 16px;border-bottom:1px solid var(--color-border);">' +
      '<div style="font-weight:600;color:var(--color-text);font-size:14px;">审计员</div>' +
      '<div style="font-size:12px;color:var(--color-text-muted);">auditor@audit.gov.cn</div></div>' +
      '<a href="settings.html" class="user-menu-item"><i class="bi bi-gear"></i> 系统设置</a>' +
      '<a href="#" class="user-menu-item" onclick="AuditWorkbench.showProfileModal(event)"><i class="bi bi-person"></i> 个人资料</a>' +
      '<a href="#" class="user-menu-item" onclick="AuditWorkbench.showPasswordModal(event)"><i class="bi bi-lock"></i> 修改密码</a>' +
      '<div style="border-top:1px solid var(--color-border);">' +
      '<a href="#" class="user-menu-item" style="color:var(--color-text-muted);" onclick="AuditWorkbench.logout(event)"><i class="bi bi-box-arrow-right"></i> 退出登录</a></div>' +
      '</div></div></div>';
  },

  renderSidebar() {
    var s = document.getElementById('aw-sidebar');
    if (!s) return;
    var h = '', self = this;

    // Find which parent group the current page belongs to
    var activeGroup = null;
    this.nav.forEach(function(item) {
      if (item.children) {
        if (self.currentPage === item.id) activeGroup = item.id;
        item.children.forEach(function(c) {
          if (self.currentPage === c.id) activeGroup = item.id;
        });
      }
    });

    this.nav.forEach(function(item) {
      if (item.type === 'divider') {
        h += '<div class="sidebar-section"><div class="sidebar-title">' + item.label + '</div></div>';
      } else if (!item.children) {
        // Simple link
        h += '<a class="sidebar-link' + (self.currentPage === item.id ? ' active' : '') +
             '" href="' + item.href + '" data-nav="' + item.id + '" title="' + item.label + '">' +
             '<i class="bi ' + item.icon + '"></i> <span>' + item.label + '</span></a>';
      } else {
        // Expandable group
        var isOpen = (activeGroup === item.id);
        h += '<div class="sidebar-group' + (isOpen ? ' open' : '') + '" data-group="' + item.id + '">' +
             '<div class="sidebar-link sidebar-group-toggle" onclick="AuditWorkbench.toggleGroup(\'' + item.id + '\', event)" data-group="' + item.id + '" title="' + item.label + '">' +
             '<i class="bi ' + item.icon + '"></i> <span>' + item.label + '</span>' +
             '<i class="bi bi-chevron-' + (isOpen ? 'down' : 'right') + ' group-arrow" style="margin-left:auto;font-size:12px;opacity:0.5;"></i>' +
             '</div>' +
             '<div class="sidebar-submenu" style="' + (isOpen ? '' : 'display:none;') + '">';
        item.children.forEach(function(child) {
          h += '<a class="sidebar-sublink' + (self.currentPage === child.id ? ' active' : '') +
               '" href="' + child.href + '" data-nav="' + child.id + '" title="' + child.label + '">' +
               '<i class="bi ' + child.icon + '"></i> <span>' + child.label + '</span></a>';
        });
        h += '</div></div>';
      }
    });
    // Add sidebar toggle button (floating on the right edge)
    h += '<button class="sidebar-toggle-btn" onclick="AuditWorkbench.toggleSidebar()" title="折叠/展开导航">' +
         '<i class="bi bi-chevron-left"></i></button>';
    s.innerHTML = h;

    // Restore collapsed state
    if (localStorage.getItem('aw_sidebar_collapsed') === '1') {
      setTimeout(function() { AuditWorkbench.collapseSidebar(); }, 100);
    }
  },

  toggleSidebar: function() {
    var s = document.getElementById('aw-sidebar');
    var c = document.querySelector('.app-content');
    var btn = document.querySelector('.sidebar-toggle-btn');
    if (!s) return;
    var cl = s.classList.toggle('collapsed');
    if (c) c.classList.toggle('expanded');
    if (btn) {
      btn.innerHTML = cl ? '<i class="bi bi-chevron-right"></i>' : '<i class="bi bi-chevron-left"></i>';
    }
    localStorage.setItem('aw_sidebar_collapsed', cl ? '1' : '0');
  },

  collapseSidebar: function() {
    var s = document.getElementById('aw-sidebar');
    var c = document.querySelector('.app-content');
    var btn = document.querySelector('.sidebar-toggle-btn');
    if (!s || s.classList.contains('collapsed')) return;
    s.classList.add('collapsed');
    if (c) c.classList.add('expanded');
    if (btn) btn.innerHTML = '<i class="bi bi-chevron-right"></i>';
  },

  /** Toggle sidebar accordion group */
  toggleGroup: function(groupId, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    var group = document.querySelector('.sidebar-group[data-group="' + groupId + '"]');
    if (!group) return;

    var isOpen = group.classList.contains('open');
    // Close all other groups
    document.querySelectorAll('.sidebar-group.open').forEach(function(g) {
      if (g !== group) {
        g.classList.remove('open');
        var sm = g.querySelector('.sidebar-submenu');
        if (sm) sm.style.display = 'none';
        var arrow = g.querySelector('.group-arrow');
        if (arrow) { arrow.className = arrow.className.replace('bi-chevron-down', 'bi-chevron-right'); }
      }
    });

    // Toggle this group
    if (isOpen) {
      group.classList.remove('open');
      var sm = group.querySelector('.sidebar-submenu');
      if (sm) sm.style.display = 'none';
    } else {
      group.classList.add('open');
      var sm = group.querySelector('.sidebar-submenu');
      if (sm) sm.style.display = 'block';
    }
    // Update arrow
    var arrow = group.querySelector('.group-arrow');
    if (arrow) {
      arrow.className = arrow.className.replace(isOpen ? 'bi-chevron-down' : 'bi-chevron-right', isOpen ? 'bi-chevron-right' : 'bi-chevron-down');
    }
    // Navigate to parent page
    var parentLink = this.nav.find(function(n) { return n.id === groupId; });
    if (parentLink && !isOpen) { window.location.href = parentLink.href; }
  },

  bindEvents() {
    if (!localStorage.getItem('aw_guide_shown')) {
      setTimeout(function() { AuditWorkbench.showGuide(); }, 500);
    }
  },

  showGuide() {
    var g = document.getElementById('aw-guide-overlay');
    if (g) g.style.display = 'flex';
  },

  closeGuide() {
    localStorage.setItem('aw_guide_shown', '1');
    var g = document.getElementById('aw-guide-overlay');
    if (g) g.style.display = 'none';
  },

  addTask(name, type) {
    // 任务真相在后端 audit_task_queue；此处仅做即时反馈 + 刷新徽章
    this.toast(name + ' 已加入后台处理队列', 'info');
    this.updateTaskBadge();
    return Date.now();
  },

  completeTask(taskId) {
    // 后端任务完成由轮询/列表反映；保留方法兼容旧调用点，刷新徽章即可
    this.updateTaskBadge();
  },

  updateTaskBadge() {
    var badge = document.getElementById('nav-task-count');
    var dot = document.querySelector('#nav-task-badge i');
    if (typeof AuditAPI === 'undefined' || !AuditAPI.tasks) return;
    AuditAPI.tasks.list({ limit: 50 }).then(function(resp) {
      var tasks = (resp && resp.success && resp.tasks) ? resp.tasks : [];
      var processing = 0, failed = 0;
      tasks.forEach(function(t) {
        if (t.status === 'processing' || t.status === 'pending') processing++;
        else if (t.status === 'failed' || t.status === 'cancelled') failed++;
      });
      if (badge) {
        badge.textContent = processing;
        badge.style.display = processing > 0 ? 'block' : 'none';
        badge.style.background = 'var(--color-accent)';
        badge.title = '处理中: ' + processing + (failed > 0 ? ' | 失败: ' + failed : '');
      }
      if (dot) {
        dot.style.color = processing > 0 ? 'var(--color-warning)' : 'rgba(255,255,255,0.5)';
      }
    }).catch(function() {});
  },

  showTasks() {
    var self = this;
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    document.body.appendChild(modal);
    // 先渲染 loading 占位，再异步拉后端真实任务
    self._renderTaskModal(modal, null);
    if (typeof AuditAPI !== 'undefined' && AuditAPI.tasks) {
      AuditAPI.tasks.list({ limit: 10 }).then(function(resp) {
        var tasks = (resp && resp.success && resp.tasks) ? resp.tasks : [];
        self._renderTaskModal(modal, tasks);
      }).catch(function() { self._renderTaskModal(modal, []); });
    } else {
      self._renderTaskModal(modal, []);
    }
  },

  _renderTaskModal(modal, tasks) {
    var loading = (tasks === null);
    var list = loading ? [] : tasks;
    var processing = 0, completed = 0, failed = 0;
    list.forEach(function(t) {
      var st = t.status;
      if (st === 'processing' || st === 'pending') processing++;
      else if (st === 'completed') completed++;
      else if (st === 'failed' || st === 'cancelled') failed++;
    });

    // Summary header
    var summaryHtml = '<div style="display:flex;gap:16px;padding:12px 0;margin-bottom:8px;">' +
      '<div style="text-align:center;flex:1;background:var(--color-bg);border-radius:var(--radius-sm);padding:8px;">' +
      '<div style="font-size:20px;font-weight:700;color:var(--color-warning);">' + processing + '</div><div style="font-size:11px;color:var(--color-text-muted);">处理中</div></div>' +
      '<div style="text-align:center;flex:1;background:var(--color-bg);border-radius:var(--radius-sm);padding:8px;">' +
      '<div style="font-size:20px;font-weight:700;color:var(--color-success);">' + completed + '</div><div style="font-size:11px;color:var(--color-text-muted);">已完成</div></div>' +
      '<div style="text-align:center;flex:1;background:var(--color-bg);border-radius:var(--radius-sm);padding:8px;">' +
      '<div style="font-size:20px;font-weight:700;color:var(--color-accent);">' + failed + '</div><div style="font-size:11px;color:var(--color-text-muted);">失败</div></div></div>';

    var itemsHtml = loading
      ? '<p style="text-align:center;color:var(--color-text-muted);padding:20px;"><span class="pulse">●</span> 正在加载任务...</p>'
      : summaryHtml;
    if (!loading) {
      if (list.length === 0) {
        itemsHtml += '<p style="text-align:center;color:var(--color-text-muted);padding:20px;">暂无后台任务</p>';
      }
      list.forEach(function(t) {
        var name = t.task_name || t.name || '任务';
        var type = t.task_type || t.type || '';
        var st = t.status;
        var isProc = (st === 'processing' || st === 'pending');
        var isDone = (st === 'completed');
        var icon = type === 'ocr' ? 'bi-file-earmark-text' : 'bi-cpu';
        var color = isDone ? 'var(--color-success)' : (isProc ? 'var(--color-warning)' : 'var(--color-accent)');
        var prog = (typeof t.progress === 'number') ? t.progress : (isProc ? 65 : 100);
        var label = isDone ? '已完成' : (isProc ? ('处理中 ' + prog + '%') : '失败');
        itemsHtml += '<div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--color-border);">' +
          '<i class="bi ' + icon + '" style="font-size:18px;color:' + color + ';"></i>' +
          '<div style="flex:1;"><div style="font-size:14px;">' + name + '</div><div style="font-size:12px;color:var(--color-text-muted);">' + label + '</div></div>' +
          (isProc ? '<div class="progress" style="width:60px;"><div class="progress-bar" style="width:' + Math.max(15, prog) + '%;animation:pulse 1.5s infinite;"></div></div>' : '') +
          '</div>';
      });
    }
    var footerHtml = '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;">' +
      '<a href="index.html" style="font-size:13px;">在首页待办中查看详情 &rarr;</a>' +
      '<button class="btn btn-sm btn-outline" onclick="AuditWorkbench.showTasks();this.closest(\'[style*=fixed]\').remove()"><i class="bi bi-arrow-clockwise"></i> 刷新</button>' +
      '</div>';

    modal.innerHTML = '<div style="background:#fff;border-radius:var(--radius-lg);max-width:500px;width:90%;padding:24px;box-shadow:var(--shadow-lg);">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">' +
      '<h3 style="margin:0;"><i class="bi bi-hourglass-split"></i> 后台任务</h3>' +
      '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="background:none;border:none;font-size:20px;cursor:pointer;">&times;</button></div>' +
      itemsHtml + footerHtml + '</div>';
    modal.onclick = function(e) { if (e.target === modal) modal.remove(); };
  },

  /** Toggle user dropdown */
  toggleUserMenu: function(event) {
    event.stopPropagation();
    var menu = document.getElementById('user-menu');
    if (!menu) return;
    menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
  },

  /** Show profile modal */
  showProfileModal: function(event) {
    event.preventDefault();
    document.getElementById('user-menu').style.display = 'none';
    var m = document.createElement('div');
    m.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    m.innerHTML = '<div style="background:#fff;border-radius:var(--radius-lg);max-width:450px;width:90%;padding:24px;box-shadow:var(--shadow-lg);">' +
      '<h3 style="margin-bottom:16px;"><i class="bi bi-person"></i> 个人资料</h3>' +
      '<div class="form-group"><label class="form-label">用户名</label><input class="form-input" value="审计员"></div>' +
      '<div class="form-group"><label class="form-label">邮箱</label><input class="form-input" value="auditor@audit.gov.cn"></div>' +
      '<div class="form-group"><label class="form-label">所属部门</label><input class="form-input" value="审计业务处"></div>' +
      '<div style="display:flex;gap:8px;justify-content:flex-end;">' +
      '<button class="btn btn-outline" onclick="this.closest(\'[style*=fixed]\').remove()">取消</button>' +
      '<button class="btn btn-primary" onclick="this.closest(\'[style*=fixed]\').remove();AuditWorkbench.toast(\'资料已更新\',\'success\')">保存</button></div></div>';
    m.addEventListener('click', function(e) { if (e.target === this) this.remove(); });
    document.body.appendChild(m);
  },

  /** Show password modal */
  showPasswordModal: function(event) {
    event.preventDefault();
    document.getElementById('user-menu').style.display = 'none';
    var m = document.createElement('div');
    m.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    m.innerHTML = '<div style="background:#fff;border-radius:var(--radius-lg);max-width:420px;width:90%;padding:24px;box-shadow:var(--shadow-lg);">' +
      '<h3 style="margin-bottom:16px;"><i class="bi bi-lock"></i> 修改密码</h3>' +
      '<div class="form-group"><label class="form-label">当前密码</label><input class="form-input" type="password" placeholder="输入当前密码"></div>' +
      '<div class="form-group"><label class="form-label">新密码</label><input class="form-input" type="password" placeholder="输入新密码"></div>' +
      '<div class="form-group"><label class="form-label">确认新密码</label><input class="form-input" type="password" placeholder="再次输入新密码"></div>' +
      '<div style="display:flex;gap:8px;justify-content:flex-end;">' +
      '<button class="btn btn-outline" onclick="this.closest(\'[style*=fixed]\').remove()">取消</button>' +
      '<button class="btn btn-accent" onclick="this.closest(\'[style*=fixed]\').remove();AuditWorkbench.toast(\'密码已修改\',\'success\')">确认修改</button></div></div>';
    m.addEventListener('click', function(e) { if (e.target === this) this.remove(); });
    document.body.appendChild(m);
  },

  /** Logout */
  logout: function(event) {
    event.preventDefault();
    document.getElementById('user-menu').style.display = 'none';
    if (confirm('确认退出登录？')) {
      this.toast('已退出登录', 'info');
    }
  },

  toast(message, type) {
    type = type || 'info';
    var container = document.getElementById('aw-toast-container');
    if (!container) return;
    var icons = { info: 'bi-info-circle', success: 'bi-check-circle', warning: 'bi-exclamation-triangle', danger: 'bi-x-circle' };
    var toast = document.createElement('div');
    toast.className = 'alert alert-' + type + ' fade-in';
    toast.style.cssText = 'margin-bottom:8px;min-width:280px;';
    toast.innerHTML = '<i class="bi ' + (icons[type] || icons.info) + '"></i> ' + message;
    container.appendChild(toast);
    setTimeout(function() { toast.remove(); }, 4000);
  },

  /** 读取当前项目记忆（集中入口；方案C 切 URL 参数时只需改这里）*/
  getProjectMemory() {
    try { return JSON.parse(localStorage.getItem('aw_project_memory') || '{}') || {}; }
    catch(e) { return {}; }
  },

  /** 方案B 回查校验：若 memory 带 project_id，向后端核对并刷新缓存（自愈 stale）。
   *  返回 Promise<最新 memory>；无 pid 或后端不可用时原样返回缓存，绝不抛错。*/
  refreshProjectMemory() {
    var mem = this.getProjectMemory();
    var pid = mem.project_id || mem.id || '';
    if (!pid || typeof AuditAPI === 'undefined' || !AuditAPI.projects || !AuditAPI.projects.get) {
      return Promise.resolve(mem);
    }
    return AuditAPI.projects.get(pid).then(function(resp) {
      if (!resp || !resp.success) return mem;
      var p = resp.project || {};
      var fresh = {};
      for (var k in mem) { fresh[k] = mem[k]; }   // 浅拷贝，保留 focus/auditItem/summary 等临时字段
      fresh.project_id = pid;
      if (p.name || p.title) fresh.title = p.name || p.title;
      if (p.audit_type || p.domain) fresh.domain = p.audit_type || p.domain;
      if (p.audited_unit || p.unit) fresh.unit = p.audited_unit || p.unit;
      if (p.target_level || p.level) fresh.level = p.target_level || p.level;
      if (p.objective) fresh.objective = p.objective;
      if (p.scope) fresh.scope = p.scope;
      // 仅当权威字段确实变了才写回，避免无谓写入
      if (fresh.title !== mem.title || fresh.domain !== mem.domain) {
        try { localStorage.setItem('aw_project_memory', JSON.stringify(fresh)); } catch(e) {}
      }
      return fresh;
    }).catch(function() { return mem; });
  }
};

document.addEventListener('DOMContentLoaded', function() {
  var pageId = document.body.dataset.page || 'home';
  AuditWorkbench.init(pageId);
});
