/**
 * AuditWorkbench — 知识工坊 (三向关联)
 * Phase 6: 真实API数据 + 前端适配层
 */
const KnowledgeWorkshop = {
  currentTab: 'violations',

  violations: [],
  regulations: [],
  cases: [],
  _loaded: false,

  init() {
    var self = this;
    this.loadData().then(function() {
      self.renderViolations(self.violations);
      self.renderRegulations();
      self.renderCases();
    });
  },

  /** 从后端API加载真实数据，映射为前端期望字段名 */
  loadData() {
    if (this._loaded) return Promise.resolve();
    var self = this;
    return Promise.all([
      // 违规行为 → 映射: violation_title→name, description→desc, expression_text→expr
      fetch('/api/audit/knowledge/violations?per_page=200').then(function(r){return r.json();}).then(function(d){
        self.violations = (d.violations || []).map(function(v){
          // 从描述文本中提取法规引用（如 《招标投标法》）
          var desc = v.description || '';
          var lawMatch = desc.match(/《[^》]+》/g);
          var lawText = lawMatch ? lawMatch.slice(0,2).join(' ') : '待关联法规';
          return {
            id: 'v' + (v.id),
            name: v.violation_title || '',
            domain: (v.category_path || '').split('/')[0] || '综合审计',
            desc: desc,
            expr: v.expression_text || '',
            law: lawText,
            cases: 0,
            materials: []
          };
        });
        // API不可用时保留已加载数据
      }).catch(function(){ return []; }),

      // 法规 → 映射: title, potency_level, timeliness
      fetch('/api/audit/knowledge/regulations?per_page=100').then(function(r){return r.json();}).then(function(d){
        self.regulations = (d.regulations || []).map(function(l){
          return {
            title: (l.title||'') ? '《' + (l.title||'') + '》' : '',
            no: l.issue_no || '', unit: l.issue_unit || '',
            level: l.potency_level || '', date: l.issue_date || '',
            status: l.timeliness || '', related: (l.id||'')
          };
        });
      }).catch(function(){ return []; }),

      // 案例 → 映射
      fetch('/api/audit/cases?limit=20').then(function(r){return r.json();}).then(function(d){
        self.cases = (d.cases || []).map(function(c){
          return {
            title: c.title || '',
            domain: c.domain || '',
            violation: '', law: '',
            amount: c.involved_amount ? ('¥' + Number(c.involved_amount||0).toLocaleString()) : '',
            method: c.case_summary || ''
          };
        });
      }).catch(function(){ return []; })
    ]).then(function(){ self._loaded = true; });
  },

  switchTab(tab) {
    this.currentTab = tab;
    document.querySelectorAll('.kw-tab').forEach(function(el){ el.classList.remove('active'); });
    document.querySelectorAll('.kw-panel').forEach(function(el){ el.classList.remove('active'); });
    var idx = tab==='violations'?1:tab==='regulations'?2:3;
    var tabEl = document.querySelector('.kw-tab:nth-child('+idx+')');
    if(tabEl) tabEl.classList.add('active');
    var panel = document.getElementById('panel-' + tab);
    if(panel) panel.classList.add('active');
    if (tab === 'regulations') this.renderRegulations();
    if (tab === 'cases') this.renderCases();
  },

  filterViolations(query) {
    var q = (query || '').toLowerCase();
    var filtered = this.violations.filter(function(v){
      return !q || v.name.indexOf(q)>=0 || v.desc.indexOf(q)>=0 || v.domain.indexOf(q)>=0;
    });
    this.renderViolations(filtered);
  },

  renderViolations(list) {
    document.getElementById('violations-list').innerHTML = list.map(function(v){
      return '<div class="association-card" onclick="KnowledgeWorkshop.showViolationDetail(\''+v.id+'\')" title="点击查看详情">'+
        '<div style="display:flex;justify-content:space-between;align-items:start;">'+
          '<div style="flex:1;">'+
            '<div style="display:flex;align-items:center;gap:8px;">'+
              '<strong style="font-size:16px;">'+v.name+'</strong>'+
              '<span class="badge badge-primary">'+v.domain+'</span>'+
            '</div>'+
            '<div style="margin-top:4px;font-size:14px;color:var(--color-text-muted);">'+v.desc+'</div>'+
          '</div>'+
          '<div style="text-align:right;">'+
            '<span class="badge badge-muted">'+v.cases+' 案例</span>'+
            '<div style="margin-top:4px;font-size:12px;color:var(--color-text-muted);">'+
              '<i class="bi bi-arrow-right-circle"></i> 点击查看</div></div></div>'+
        '<div class="assoc-links" onclick="event.stopPropagation();">'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.showAssociation(\'law\',\''+v.id+'\')">'+
            '<i class="bi bi-link-45deg"></i> 关联法规: '+v.law+'</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.showAssociation(\'case\',\''+v.id+'\')">'+
            '<i class="bi bi-files"></i> 关联案例 ('+v.cases+'个)</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.showExpression(\''+v.id+'\')">'+
            '<i class="bi bi-diagram-3"></i> 查看比对逻辑</span>'+
          '<span class="assoc-link-item" onclick="location.href=\'analysis.html\'" style="margin-left:auto;">'+
            '<i class="bi bi-play-fill"></i> 以此为模型启动分析</span></div></div>';
    }).join('');
  },

  /** 点击违规卡片 → 详情弹窗 */
  showViolationDetail: function(vid) {
    var v = this.violations.find(function(x){ return x.id === vid; });
    if(!v) return;
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:700px;width:95%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
      '<div style="padding:24px 28px;border-bottom:1px solid var(--color-border);display:flex;align-items:center;gap:12px;">'+
        '<div style="flex:1;"><h3 style="margin:0 0 4px;">'+v.name+'</h3>'+
        '<span class="badge badge-primary">'+v.domain+'</span> <span class="badge badge-muted">'+v.cases+' 个关联案例</span></div>'+
        '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="background:none;border:none;font-size:24px;cursor:pointer;color:var(--color-text-muted);">&times;</button></div>'+
      '<div style="padding:16px 28px;">'+
        '<div class="card" style="margin-bottom:12px;"><div class="card-header"><h4>违规描述</h4></div>'+
        '<div style="padding:12px;font-size:14px;line-height:1.8;">'+v.desc+'</div></div>'+
        '<div class="card" style="margin-bottom:12px;"><div class="card-header"><h4>比对逻辑（违规表达式）</h4></div>'+
        '<div style="padding:12px;font-family:monospace;font-size:13px;background:var(--color-bg);border-radius:6px;line-height:1.8;">'+v.expr+'</div></div>'+
        '<div class="card" style="margin-bottom:12px;"><div class="card-header"><h4>关联法规依据</h4></div>'+
        '<div style="padding:12px;font-size:14px;">'+v.law+'</div></div>'+
        '<div class="card" style="margin-bottom:12px;"><div class="card-header"><h4>审计所需资料</h4></div>'+
        '<div style="padding:12px;font-size:13px;line-height:1.8;">'+
          (v.materials && v.materials.length ? v.materials.map(function(m){return '<div>📄'+m+'</div>';}).join('') :
          '<div>📄相关审计资料（请参考法规条款确定）</div>')+
        '</div></div>'+
        '<div style="display:flex;gap:8px;padding:16px 28px;border-top:1px solid var(--color-border);">'+
          '<button class="btn btn-accent btn-lg" style="flex:1;" onclick="location.href=\'analysis.html\';this.closest(\'[style*=fixed]\').remove();">'+
            '<i class="bi bi-rocket-takeoff"></i> 以此为模型启动智能分析</button>'+
          '<button class="btn btn-outline" onclick="this.closest(\'[style*=fixed]\').remove();">关闭</button></div>'+
      '</div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);
  },

  showAssociation(type, id) {
    if (type === 'law') {
      this.switchTab('regulations');
      AuditWorkbench.toast('已定位到关联法规', 'info');
    } else {
      this.switchTab('cases');
      AuditWorkbench.toast('已定位到关联案例', 'info');
    }
  },

  showExpression(vid) {
    var v = this.violations.find(function(x){ return x.id === vid; });
    if (!v) return;
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:var(--radius-lg);max-width:600px;width:90%;padding:24px;box-shadow:var(--shadow-lg);">'+
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">'+
          '<h3 style="margin:0;">'+v.name+' — 比对逻辑</h3>'+
          '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="background:none;border:none;font-size:20px;cursor:pointer;">&times;</button></div>'+
        '<p style="color:var(--color-text-muted);margin-bottom:16px;">'+v.desc+'</p>'+
        '<div style="background:var(--color-bg);border-radius:var(--radius-md);padding:16px;">'+
          '<div style="font-size:12px;color:var(--color-text-muted);margin-bottom:8px;">比对逻辑</div>'+
          '<div style="font-family:var(--font-mono);font-size:13px;line-height:1.8;">'+v.expr+'</div></div>'+
        '<div class="alert alert-info" style="margin-top:12px;"><strong>关联法规:</strong> '+v.law+'</div>'+
        '<button class="btn btn-primary" style="margin-top:12px;" onclick="location.href=\'analysis.html\';this.closest(\'[style*=fixed]\').remove();">'+
          '<i class="bi bi-play-fill"></i> 使用此模型启动智能分析</button></div>';
    modal.addEventListener('click', function(e) { if (e.target === this) this.remove(); });
    document.body.appendChild(modal);
  },

  renderRegulations() {
    if (this.regulations.length === 0) {
      document.getElementById('regulations-list').innerHTML = '<div style="padding:40px;text-align:center;color:var(--color-text-muted);font-size:14px;">暂无法规数据</div>';
      return;
    }
    document.getElementById('regulations-list').innerHTML = this.regulations.map(function(r){
      return '<div class="association-card">'+
        '<div style="display:flex;justify-content:space-between;align-items:start;">'+
          '<div><strong style="font-size:16px;">'+r.title+'</strong>'+
            '<div style="font-size:13px;color:var(--color-text-muted);margin-top:2px;">'+r.no+' | '+r.unit+' | '+r.date+'</div></div>'+
          '<span class="badge badge-success">'+r.status+'</span></div>'+
        '<div class="assoc-links">'+
          '<span class="assoc-link-item"><i class="bi bi-tag"></i> '+r.level+'</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.showRegulationGraph(\''+r.related+'\')">'+
            '<i class="bi bi-diagram-2"></i> 关系链: '+r.related+'</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.showClauses(\''+r.related+'\')">'+
            '<i class="bi bi-journal-text"></i> 查看条款</span>'+
          '<a class="trace-link" href="#" style="margin-left:auto;"><i class="bi bi-link-45deg"></i> 溯源</a></div></div>';
    }).join('');
  },

  /** 点击"关系链" → 详情弹窗 */
  showRegulationGraph: function(lawId) {
    if (!lawId || lawId.length < 5) { AuditWorkbench.toast('暂无关系链数据','info'); return; }
    var self = this;
    fetch('/api/audit/knowledge/regulation/' + lawId + '/graph').then(function(r){return r.json();}).then(function(d){
      if(!d.success || !d.graph){ AuditWorkbench.toast('暂无关系链数据','info'); return; }
      var g = d.graph;
      // 上位法链
      var superiorHtml = '';
      if (g.superior_chain && g.superior_chain.length) {
        superiorHtml = '<div style="margin-bottom:8px;"><strong>🔼 上位法链 ('+g.superior_chain.length+'部)</strong></div>' +
          g.superior_chain.slice(0,10).map(function(l){
            return '<div style="padding:4px 8px;font-size:13px;border-left:2px solid var(--color-primary);margin:2px 0;">'+
              '<span style="color:var(--color-text-muted);">L'+l.depth+'</span> '+l.title.substring(0,50)+
              ' <span class="badge badge-muted">'+ (l.potency_level||'') +'</span></div>';
          }).join('');
      }
      // 下位法
      var inferiorHtml = '';
      if (g.inferior && g.inferior.length) {
        inferiorHtml = '<div style="margin:12px 0 8px;"><strong>🔽 下位法 ('+g.inferior.length+'部)</strong></div>' +
          g.inferior.slice(0,10).map(function(l){
            return '<div style="padding:4px 8px;font-size:13px;border-left:2px solid var(--color-success);margin:2px 0;">'+
              l.title.substring(0,50)+' <span class="badge badge-muted">'+ (l.potency_level||'') +'</span></div>';
          }).join('');
      }
      // 相关法
      var relatedHtml = '';
      if (g.related && g.related.length) {
        relatedHtml = '<div style="margin:12px 0 8px;"><strong>🔗 相关法 ('+g.related.length+'部)</strong></div>' +
          g.related.slice(0,10).map(function(l){
            return '<div style="padding:4px 8px;font-size:13px;border-left:2px solid var(--color-warning);margin:2px 0;">'+
              l.title.substring(0,50)+' <span class="badge badge-muted">'+ (l.potency_level||'') +'</span></div>';
          }).join('');
      }

      var modal = document.createElement('div');
      modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
      modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:650px;width:95%;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
        '<div style="padding:20px 24px;border-bottom:1px solid var(--color-border);display:flex;align-items:center;justify-content:space-between;">'+
        '<div><h3 style="margin:0;">法规关系链</h3><div style="font-size:14px;color:var(--color-text-muted);margin-top:4px;">'+
        '中心法规: '+g.center.title+' <span class="badge badge-primary">'+ (g.center.potency_level||'') +'</span> '+
        (g.center.timeliness||'') +' · 共'+g.total_relations+'条关系</div></div>'+
        '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="background:none;border:none;font-size:24px;cursor:pointer;color:var(--color-text-muted);">&times;</button></div>'+
        '<div style="padding:16px 24px;">'+superiorHtml+inferiorHtml+relatedHtml+
        (g.history_versions && g.history_versions.length ? '<div style="margin:12px 0 8px;"><strong>🕐 历史版本 ('+g.history_versions.length+'部)</strong></div>' : '')+
        '</div></div>';
      modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
      document.body.appendChild(modal);
    }).catch(function(){ AuditWorkbench.toast('关系链加载失败','error'); });
  },

  /** 点击"查看条款" → 详情弹窗 */
  showClauses: function(lawId) {
    if (!lawId || lawId.length < 5) { AuditWorkbench.toast('暂无条款数据','info'); return; }
    fetch('/api/audit/knowledge/clauses/' + lawId).then(function(r){return r.json();}).then(function(d){
      if(!d.success || !d.total){ AuditWorkbench.toast('暂无条款数据','info'); return; }
      var clauses = (d.clauses||[]).slice(0,50);
      // 按条款类型分组
      var groups = {};
      clauses.forEach(function(c){
        var t = c.clause_type || '其他';
        if (!groups[t]) groups[t] = [];
        groups[t].push(c);
      });
      var groupHtml = '';
      for (var type in groups) {
        groupHtml += '<div style="margin-bottom:8px;"><strong style="color:var(--color-primary);">'+type+'类 ('+groups[type].length+'条)</strong></div>';
        groups[type].slice(0,8).forEach(function(c){
          groupHtml += '<div style="padding:4px 8px;font-size:13px;border-left:2px solid var(--color-border);margin:2px 0;">'+
            (c.clause_number ? '<span style="color:var(--color-accent);font-weight:600;">'+c.clause_number+'</span> ' : '')+
            (c.clause_summary||'').substring(0,80)+
            (c.audit_scenario ? '<span style="display:block;font-size:11px;color:var(--color-text-muted);">'+c.audit_scenario.substring(0,60)+'</span>' : '')+
            '</div>';
        });
      }

      var modal = document.createElement('div');
      modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
      modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:700px;width:95%;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
        '<div style="padding:20px 24px;border-bottom:1px solid var(--color-border);display:flex;align-items:center;justify-content:space-between;">'+
        '<div><h3 style="margin:0;">条款分析</h3><div style="font-size:14px;color:var(--color-text-muted);margin-top:4px;">共'+d.total+'条条款 · '+
        Object.keys(groups).length+'种类型</div></div>'+
        '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="background:none;border:none;font-size:24px;cursor:pointer;color:var(--color-text-muted);">&times;</button></div>'+
        '<div style="padding:16px 24px;">'+groupHtml+'</div></div>';
      modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
      document.body.appendChild(modal);
    }).catch(function(){ AuditWorkbench.toast('条款加载失败','error'); });
  },

  renderCases() {
    if (this.cases.length === 0) {
      document.getElementById('cases-list').innerHTML = '<div style="padding:40px;text-align:center;color:var(--color-text-muted);font-size:14px;">暂无案例数据</div>';
      return;
    }
    document.getElementById('cases-list').innerHTML = this.cases.map(function(c){
      return '<div class="association-card">'+
        '<div style="display:flex;justify-content:space-between;align-items:start;">'+
          '<div style="flex:1;"><strong style="font-size:16px;">'+c.title+'</strong>'+
            '<span class="badge badge-primary" style="margin-left:8px;">'+c.domain+'</span></div>'+
          '<span style="font-weight:600;color:var(--color-accent);">'+c.amount+'</span></div>'+
        '<div style="margin-top:4px;font-size:14px;color:var(--color-text-muted);">'+c.method+'</div>'+
        '<div class="assoc-links">'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.switchTab(\'violations\')">'+
            '<i class="bi bi-exclamation-triangle"></i> 违规模型: '+c.violation+'</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.switchTab(\'regulations\')">'+
            '<i class="bi bi-journal-text"></i> 法规: '+c.law+'</span>'+
          '<a class="trace-link" href="#" style="margin-left:auto;"><i class="bi bi-link-45deg"></i> 溯源</a></div></div>';
    }).join('');
  }
};

document.addEventListener('DOMContentLoaded', function() {
  KnowledgeWorkshop.init();
  var hash = window.location.hash.replace('#', '');
  if (hash === 'regulations') KnowledgeWorkshop.switchTab('regulations');
  if (hash === 'cases') KnowledgeWorkshop.switchTab('cases');
  if (hash === 'violations') KnowledgeWorkshop.switchTab('violations');
});
