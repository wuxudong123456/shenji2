/**
 * AuditWorkbench — 知识工坊 (三向关联)
 * Phase 6: 真实API数据 + 前端适配层
 */
const KnowledgeWorkshop = {
  currentTab: 'violations',

  violations: [],
  regulations: [],
  cases: [],
  categories: [],
  currentCategory: '',
  violationsTotal: 0,
  caseDomains: [],
  currentCaseDomain: '',
  potencyLevels: [],
  currentPotency: '',
  regulationsTotal: 0,
  casesTotal: 0,
  _loaded: false,

  // ── 分页状态（每次拉 PAGE_SIZE 条，点"加载更多"时累加）──
  PAGE_SIZE: 100,
  violationsPage: 0,    violationsHasMore: false,
  regulationsPage: 0,   regulationsHasMore: false,
  casesPage: 0,         casesHasMore: false,
  // ── 服务端搜索词 + 防抖定时器 + 请求序号（丢弃过期响应防乱序）──
  violationsQuery: '', regulationsQuery: '', casesQuery: '',
  _searchTimers: {},
  _reqSeq: {},

  init() {
    var self = this;
    this.loadData().then(function() {
      // 违规 Tab 默认激活，loadViolations 已渲染；法规/案例 Tab 在 switchTab 时按需渲染
    });
  },

  /** 从后端API加载真实数据，映射为前端期望字段名 */
  loadData() {
    if (this._loaded) return Promise.resolve();
    var self = this;
    return Promise.all([
      // 违规行为 → 按分类服务端加载（映射: violation_title→name, description→desc, expression_text→expr）
      this.loadViolations(),

      // 法规 → 按效力级别加载
      this.loadRegulations(),

      // 案例 → 按领域加载
      this.loadCases(),
    ]).then(function(){ self._loaded = true; });
  },

  /** 按当前分类 + 搜索词分页加载违规行为（服务端筛选），渲染列表 + 分类下拉框 */
  loadViolations(append) {
    var self = this;
    var seq = (this._reqSeq['violations'] = (this._reqSeq['violations'] || 0) + 1);
    var page = append ? this.violationsPage + 1 : 1;
    var url = '/api/audit/knowledge/violations?per_page=' + this.PAGE_SIZE + '&page=' + page;
    if (this.currentCategory) url += '&category=' + encodeURIComponent(this.currentCategory);
    if (this.violationsQuery) url += '&q=' + encodeURIComponent(this.violationsQuery);
    return fetch(url).then(function(r){return r.json();}).then(function(d){
      if (self._reqSeq['violations'] !== seq) return false; // 丢弃过期响应
      self.violationsTotal = d.total || 0;
      var mapped = (d.violations || []).map(function(v){
        // 从描述文本中提取法规引用（如 《招标投标法》）
        var desc = v.description || '';
        var lawMatch = desc.match(/《[^》]+》/g);
        var lawText = lawMatch ? lawMatch.slice(0,2).join(' ') : '待关联法规';
        return {
          id: 'v' + (v.id),
          vid: v.id,
          name: v.violation_title || '',
          domain: (v.category_path || '').split('/')[0] || '综合审计',
          desc: desc,
          expr: v.expression_text || '',
          law: lawText,
          cases: v.case_count || 0,
          caseIds: v.case_ids ? JSON.parse(v.case_ids) : [],
          materials: []
        };
      });
      self.violations = append ? self.violations.concat(mapped) : mapped;
      self.violationsPage = page;
      self.violationsHasMore = self.violations.length < self.violationsTotal;
      // 首次加载时填充分类下拉框
      if (d.categories && d.categories.length && !self.categories.length) {
        self.categories = d.categories;
        self.renderCategorySelect();
      }
      self.updateViolationBadge();
      self.renderViolations(self.violations);
      self.renderLoadMore('violations');
      return true;
    }).catch(function(){ return false; });
  },

  /** 分类下拉框变更 → 服务端重新加载 */
  onCategoryChange(cat) {
    this.currentCategory = cat || '';
    // 重置搜索词，避免旧搜索词过滤新分类数据
    this.violationsQuery = '';
    var searchInput = document.querySelector('#panel-violations .form-input');
    if (searchInput) searchInput.value = '';
    this.loadViolations(false);
  },

  /** 渲染分类下拉框选项 */
  renderCategorySelect() {
    var sel = document.getElementById('violation-category');
    if (!sel) return;
    var html = '<option value="">全部类型 (' + this.violationsTotal.toLocaleString() + ')</option>';
    this.categories.forEach(function(c){
      html += '<option value="' + c + '">' + c + '</option>';
    });
    sel.innerHTML = html;
    sel.value = this.currentCategory;
  },

  /** 更新违规 tab 徽章数量 */
  updateViolationBadge() {
    var badge = document.getElementById('violation-count-badge');
    if (badge) badge.textContent = this.violationsTotal.toLocaleString();
  },

  /** 按当前效力级别 + 搜索词分页加载法规（服务端筛选），渲染列表 + 效力级别下拉框 */
  loadRegulations(append) {
    var self = this;
    var seq = (this._reqSeq['regulations'] = (this._reqSeq['regulations'] || 0) + 1);
    var page = append ? this.regulationsPage + 1 : 1;
    var url = '/api/audit/knowledge/regulations?per_page=' + this.PAGE_SIZE + '&page=' + page;
    if (this.currentPotency) url += '&potency_level=' + encodeURIComponent(this.currentPotency);
    if (this.regulationsQuery) url += '&q=' + encodeURIComponent(this.regulationsQuery);
    return fetch(url).then(function(r){return r.json();}).then(function(d){
      if (self._reqSeq['regulations'] !== seq) return false; // 丢弃过期响应
      var mapped = (d.regulations || []).map(function(l){
        return {
          title: (l.title||'') ? '《' + (l.title||'') + '》' : '',
          no: l.issue_no || '', unit: l.issue_unit || '',
          level: l.potency_level || '', date: l.issue_date || '',
          status: l.timeliness || '', related: (l.id||'')
        };
      });
      self.regulations = append ? self.regulations.concat(mapped) : mapped;
      self.regulationsPage = page;
      self.regulationsTotal = d.total || 0;
      self.regulationsHasMore = self.regulations.length < self.regulationsTotal;
      // 首次加载时填充效力级别下拉框
      if (d.filters && d.filters.potency_levels && d.filters.potency_levels.length && !self.potencyLevels.length) {
        self.potencyLevels = d.filters.potency_levels;
        self.renderPotencySelect();
      }
      var regBadge = document.getElementById('regulation-count-badge');
      if (regBadge && d.total) regBadge.textContent = Number(d.total).toLocaleString();
      // 仅当前激活法规 Tab 才渲染（按需渲染，避免隐藏面板大 DOM）
      if (self.currentTab === 'regulations') self.renderRegulations();
      self.renderLoadMore('regulations');
      return true;
    }).catch(function(){ return false; });
  },

  /** 效力级别下拉框变更 → 服务端重新加载 */
  onPotencyChange(level) {
    this.currentPotency = level || '';
    this.regulationsQuery = '';
    var searchInput = document.querySelector('#panel-regulations .form-input');
    if (searchInput) searchInput.value = '';
    this.loadRegulations(false);
  },

  /** 渲染效力级别下拉框选项 */
  renderPotencySelect() {
    var sel = document.getElementById('regulation-potency');
    if (!sel) return;
    var html = '<option value="">全部效力级别</option>';
    this.potencyLevels.forEach(function(p){
      html += '<option value="' + p + '">' + p + '</option>';
    });
    sel.innerHTML = html;
    sel.value = this.currentPotency;
  },

  /** 搜索框输入 → 防抖后服务端搜索（重置到第 1 页） */
  onSearchInput(tab, value) {
    var self = this;
    var q = (value || '').trim();
    var queryKey = tab + 'Query';
    if (this[queryKey] === q) return; // 内容未变化
    this[queryKey] = q;
    clearTimeout(this._searchTimers[tab]);
    this._searchTimers[tab] = setTimeout(function() {
      if (tab === 'violations') self.loadViolations(false);
      else if (tab === 'regulations') self.loadRegulations(false);
      else self.loadCases(false);
    }, 300);
  },

  /** "加载更多" → 累加下一页 */
  loadMore(tab) {
    if (tab === 'violations') this.loadViolations(true);
    else if (tab === 'regulations') this.loadRegulations(true);
    else this.loadCases(true);
  },

  /** 渲染加载更多按钮（隐藏/显示 + 已加载进度文案） */
  renderLoadMore(tab) {
    var hasMoreMap = { violations: 'violationsHasMore', regulations: 'regulationsHasMore', cases: 'casesHasMore' };
    var loadedMap  = { violations: 'violations', regulations: 'regulations', cases: 'cases' };
    var totalMap   = { violations: 'violationsTotal', regulations: 'regulationsTotal', cases: 'casesTotal' };
    var el = document.getElementById(tab + '-loadmore');
    if (!el) return;
    var hasMore = !!this[hasMoreMap[tab]];
    el.style.display = hasMore ? '' : 'none';
    var btn = el.querySelector('.kw-loadmore-btn');
    if (btn) {
      var loaded = this[loadedMap[tab]].length;
      var total = Number(this[totalMap[tab]] || 0).toLocaleString();
      btn.textContent = hasMore
        ? ('加载更多（已加载 ' + loaded + ' / ' + total + '）')
        : ('已全部加载（' + loaded + ' / ' + total + '）');
    }
  },

  /** 按当前领域 + 搜索词分页加载案例（服务端筛选），渲染列表 + 领域下拉框 */
  loadCases(append) {
    var self = this;
    var seq = (this._reqSeq['cases'] = (this._reqSeq['cases'] || 0) + 1);
    var offset = append ? this.cases.length : 0;
    var url = '/api/audit/cases?limit=' + this.PAGE_SIZE + '&offset=' + offset;
    if (this.currentCaseDomain) url += '&domain=' + encodeURIComponent(this.currentCaseDomain);
    if (this.casesQuery) url += '&q=' + encodeURIComponent(this.casesQuery);
    return fetch(url).then(function(r){return r.json();}).then(function(d){
      if (self._reqSeq['cases'] !== seq) return false; // 丢弃过期响应
      var mapped = (d.cases || []).map(function(c){
        return {
          id: c.id,
          title: c.title || '',
          domain: c.domain || '',
          violation: c.violation_names || '',
          law: c.law_names || '',
          amount: c.involved_amount ? ('¥' + Number(c.involved_amount||0).toLocaleString()) : '',
          method: c.case_summary || ''
        };
      });
      self.cases = append ? self.cases.concat(mapped) : mapped;
      self.casesTotal = d.total || 0;
      self.casesHasMore = self.cases.length < self.casesTotal;
      // 首次加载时填充领域下拉框
      if (d.domains && d.domains.length && !self.caseDomains.length) {
        self.caseDomains = d.domains;
        self.renderCaseCategorySelect();
      }
      var caseBadge = document.getElementById('case-count-badge');
      if (caseBadge && d.total) caseBadge.textContent = Number(d.total).toLocaleString();
      // 仅当前激活案例 Tab 才渲染（按需渲染，避免隐藏面板大 DOM）
      if (self.currentTab === 'cases') self.renderCases();
      self.renderLoadMore('cases');
      return true;
    }).catch(function(){ return false; });
  },

  /** 案例领域下拉框变更 → 服务端重新加载 */
  onCaseCategoryChange(domain) {
    this.currentCaseDomain = domain || '';
    this.casesQuery = '';
    var searchInput = document.querySelector('#panel-cases .form-input');
    if (searchInput) searchInput.value = '';
    this.loadCases(false);
  },

  /** 渲染案例领域下拉框选项 */
  renderCaseCategorySelect() {
    var sel = document.getElementById('case-category');
    if (!sel) return;
    var html = '<option value="">全部领域</option>';
    this.caseDomains.forEach(function(d){
      html += '<option value="' + d + '">' + d + '</option>';
    });
    sel.innerHTML = html;
    sel.value = this.currentCaseDomain;
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
    // 按需渲染当前激活 Tab（数据未返回时显示空态，返回后由 load* 补渲染）
    if (tab === 'regulations') this.renderRegulations();
    else if (tab === 'cases') this.renderCases();
    else this.renderViolations(this.violations);
    this.renderLoadMore(tab);
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
            '<i class="bi bi-link-45deg"></i> 关联法规</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.openCaseById(\''+v.id+'\')">'+
            '<i class="bi bi-files"></i> 关联案例 ('+v.cases+'个)</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.showExpression(\''+v.id+'\')">'+
            '<i class="bi bi-diagram-3"></i> 查看比对逻辑</span>'+
          '<span class="assoc-link-item" onclick="location.href=\'analysis.html\'" style="margin-left:auto;">'+
            '<i class="bi bi-play-fill"></i> 以此为模型启动分析</span></div></div>';
    }).join('');
  },

  /** 点击违规卡片 → 详情弹窗（异步拉取关联法规 + 审计所需数据） */
  showViolationDetail: function(vid) {
    var self = this;
    var v = this.violations.find(function(x){ return x.id === vid; });
    if(!v) return;
    var realId = String(vid).replace(/^v/, '');
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:760px;width:95%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
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
        '<div style="padding:12px;font-size:14px;" id="detail-laws-body">加载中...</div></div>'+
        '<div class="card" style="margin-bottom:12px;display:none;" id="detail-data-card"><div class="card-header"><h4>📊 审计所需数据</h4></div>'+
        '<div style="padding:12px;font-size:13px;line-height:1.8;" id="detail-data-body">加载中...</div></div>'+
        '<div style="display:flex;gap:8px;padding:16px 28px;border-top:1px solid var(--color-border);">'+
          '<button class="btn btn-accent btn-lg" style="flex:1;" onclick="location.href=\'analysis.html\';this.closest(\'[style*=fixed]\').remove();">'+
            '<i class="bi bi-rocket-takeoff"></i> 以此为模型启动智能分析</button>'+
          '<button class="btn btn-outline" onclick="this.closest(\'[style*=fixed]\').remove();">关闭</button></div>'+
      '</div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);

    // 异步拉取详情（关联法规 laws + 审计所需数据 required_data）
    fetch('/api/audit/knowledge/violations/' + realId).then(function(r){return r.json();}).then(function(d){
      if (!d.success || !d.violation) return;
      var det = d.violation;
      var lawsBody = modal.querySelector('#detail-laws-body');
      var dataBody = modal.querySelector('#detail-data-body');
      var dataCard = modal.querySelector('#detail-data-card');

      // 关联法规依据（从关联表读取）
      if (d.laws && d.laws.length) {
        lawsBody.innerHTML = d.laws.map(function(l){
          var html = '<div style="padding:5px 0;">' + (l.title || '');
          if (l.potency_level) {
            html += ' <span style="font-size:11px;color:var(--color-primary);background:rgba(26,58,92,0.08);padding:1px 6px;border-radius:8px;margin-left:4px;">' + l.potency_level + '</span>';
          }
          if (!l.matched && !l.clause_ref) {
            html += ' <span style="font-size:11px;color:var(--color-text-muted);margin-left:4px;">（待补充）</span>';
          }
          if (l.clause_ref) {
            html += '<div style="font-size:12px;color:var(--color-text-muted);margin:2px 0 0 2px;">条款: ' + l.clause_ref + '</div>';
          }
          html += '</div>';
          return html;
        }).join('');
      } else {
        lawsBody.innerHTML = '<div style="color:var(--color-text-muted);">暂无关联法规</div>';
      }

      // 审计所需数据
      if (det.required_data) {
        dataBody.innerHTML = self.renderRequiredData(det.required_data);
        dataCard.style.display = '';
      }
    }).catch(function(){
      var lawsBody = modal.querySelector('#detail-laws-body');
      if (lawsBody) lawsBody.innerHTML = '<div style="color:var(--color-text-muted);">关联法规加载失败</div>';
    });
  },

  /** 渲染审计所需数据 JSON（Col⑧所需资料类型 + Col⑨对应数据字段） */
  renderRequiredData: function(jsonStr) {
    var parsed = null;
    try { parsed = JSON.parse(jsonStr); } catch(e) {}
    // 兼容 {"raw": "```json...```"} 包装
    if (parsed && typeof parsed === 'object' && !parsed.items && !parsed.files && !parsed.tables && parsed.raw) {
      var cleaned = String(parsed.raw).replace(/^\s*```[a-zA-Z]*\s*/, '').replace(/```\s*$/, '').trim();
      try { parsed = JSON.parse(cleaned); } catch(e) { parsed = null; }
    }
    // 新结构: {items: [{name, material_type, fields}]}
    if (parsed && typeof parsed === 'object' && parsed.items && parsed.items.length) {
      var html = '<div style="margin-bottom:6px;"><strong>📊 需要比对的数表</strong></div>';
      parsed.items.forEach(function(it, i){
        html += '<div style="padding:5px 0;">' + (i + 1) + '. ' + (it.name || '');
        if (it.material_type) {
          html += ' <span style="font-size:11px;color:var(--color-primary);background:rgba(26,58,92,0.08);padding:1px 6px;border-radius:8px;margin-left:4px;">' + it.material_type + '</span>';
        }
        if (it.fields && it.fields.length) {
          html += '<div style="font-size:12px;color:var(--color-text-muted);margin:2px 0 0 2px;">字段: ' + it.fields.join('、') + '</div>';
        }
        html += '</div>';
      });
      return html;
    }
    // 兼容旧结构 {files, tables}
    if (parsed && typeof parsed === 'object' && (parsed.files || parsed.tables)) {
      var html2 = '<div style="margin-bottom:6px;"><strong>📊 需要比对的数表</strong></div>';
      var list = (parsed.tables || []).concat(parsed.files || []);
      list.forEach(function(t, i){
        html2 += '<div style="padding:4px 0;">' + (i + 1) + '. ' + (t.tablename || t.filename || '');
        if (t.material_type) {
          html2 += ' <span style="font-size:11px;color:var(--color-primary);background:rgba(26,58,92,0.08);padding:1px 6px;border-radius:8px;margin-left:4px;">' + t.material_type + '</span>';
        }
        if (t.fields && t.fields.length) {
          html2 += '<div style="font-size:12px;color:var(--color-text-muted);margin:2px 0 0 2px;">字段: ' + t.fields.join('、') + '</div>';
        }
        html2 += '</div>';
      });
      return html2;
    }
    return '<div style="white-space:pre-wrap;">' + jsonStr + '</div>';
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

  renderRegulations(list) {
    var data = list || this.regulations;
    if (!data.length) {
      document.getElementById('regulations-list').innerHTML = '<div style="padding:40px;text-align:center;color:var(--color-text-muted);font-size:14px;">暂无法规数据</div>';
      return;
    }
    document.getElementById('regulations-list').innerHTML = data.map(function(r){
      return '<div class="association-card">'+
        '<div style="display:flex;justify-content:space-between;align-items:start;">'+
          '<div><strong style="font-size:16px;">'+r.title+'</strong>'+
            '<div style="font-size:13px;color:var(--color-text-muted);margin-top:2px;">'+r.no+' | '+r.unit+' | '+r.date+'</div></div>'+
          '<span class="badge badge-success">'+r.status+'</span></div>'+
        '<div class="assoc-links">'+
          '<span class="assoc-link-item"><i class="bi bi-tag"></i> '+r.level+'</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.showRegulationGraph(\''+r.related+'\')">'+
            '<i class="bi bi-diagram-2"></i> 关系链</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.showClauses(\''+r.related+'\')">'+
            '<i class="bi bi-journal-text"></i> 查看条款</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.showLawDetail(\''+r.related+'\')">'+
            '<i class="bi bi-link-45deg"></i> 溯源</span></div></div>';
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

  /** 点击"溯源" → 法规详情弹窗（发布机关/文号/日期/时效/正文） */
  showLawDetail: function(lawId) {
    if (!lawId || lawId.length < 5) { AuditWorkbench.toast('暂无法规详情','info'); return; }
    fetch('/api/audit/knowledge/regulation/' + lawId).then(function(r){return r.json();}).then(function(d){
      if(!d.success || !d.law){ AuditWorkbench.toast('暂无法规详情','info'); return; }
      var l = d.law;
      var title = (l.title || '').replace(/^《|》$/g, '');
      var meta = [l.issue_unit, l.issue_no, l.issue_date, l.implement_date, l.timeliness]
        .filter(function(x){ return x; }).join(' · ');
      var bodyHtml = '<div style="padding:12px;font-size:14px;line-height:1.8;">'+
        '<div><strong>《' + title + '》</strong></div>';
      if (meta) bodyHtml += '<div style="font-size:13px;color:var(--color-text-muted);margin-top:6px;">' + meta + '</div>';
      if (l.content) bodyHtml += '<div style="margin-top:14px;font-size:13px;line-height:1.8;white-space:pre-wrap;color:var(--color-text-muted);">' + l.content + '</div>';
      bodyHtml += '</div>';
      var modal = document.createElement('div');
      modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
      modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:720px;width:95%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
        '<div style="padding:20px 24px;border-bottom:1px solid var(--color-border);display:flex;align-items:center;justify-content:space-between;">'+
          '<div><h3 style="margin:0;">法规详情</h3>'+
          '<div style="font-size:14px;color:var(--color-text-muted);margin-top:4px;">'+(l.potency_level||'')+'</div></div>'+
          '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="background:none;border:none;font-size:24px;cursor:pointer;color:var(--color-text-muted);">&times;</button></div>'+
        '<div style="padding:16px 24px;">'+bodyHtml+
          '<button class="btn btn-outline" style="width:100%;margin-top:12px;" onclick="this.closest(\'[style*=fixed]\').remove();">关闭</button>'+
        '</div></div>';
      modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
      document.body.appendChild(modal);
    }).catch(function(){ AuditWorkbench.toast('法规详情加载失败','error'); });
  },

  renderCases(list) {
    var data = list || this.cases;
    if (!data.length) {
      document.getElementById('cases-list').innerHTML = '<div style="padding:40px;text-align:center;color:var(--color-text-muted);font-size:14px;">暂无案例数据</div>';
      return;
    }
    document.getElementById('cases-list').innerHTML = data.map(function(c){
      return '<div class="association-card" onclick="KnowledgeWorkshop.showCaseDetail(\''+c.id+'\')" title="点击查看详情">'+
        '<div style="display:flex;justify-content:space-between;align-items:start;">'+
          '<div style="flex:1;"><strong style="font-size:16px;">'+c.title+'</strong>'+
            '<span class="badge badge-primary" style="margin-left:8px;">'+c.domain+'</span></div>'+
          '<span style="font-weight:600;color:var(--color-accent);">'+c.amount+'</span></div>'+
        '<div style="margin-top:4px;font-size:14px;color:var(--color-text-muted);">'+c.method+'</div>'+
        '<div class="assoc-links" onclick="event.stopPropagation();">'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.switchTab(\'violations\')">'+
            '<i class="bi bi-exclamation-triangle"></i> 违规模型: '+(c.violation||'暂无')+'</span>'+
          '<span class="assoc-link-item" onclick="KnowledgeWorkshop.switchTab(\'regulations\')">'+
            '<i class="bi bi-journal-text"></i> 法规: '+(c.law||'暂无')+'</span>'+
          '<span class="assoc-link-item" style="margin-left:auto;" onclick="KnowledgeWorkshop.showCaseDetail(\''+c.id+'\')">'+
            '<i class="bi bi-arrow-right-circle"></i> 查看详情</span>'+
        '</div></div>';
    }).join('');
  },

  /** 点"关联案例" → 精确打开案例详情（单案例直接进，多案例弹列表） */
  openCaseById: function(vid) {
    var v = this.violations.find(function(x){ return x.id === vid; });
    if (!v) return;
    if (!v.caseIds || !v.caseIds.length) {
      AuditWorkbench.toast('该违规暂无关联案例', 'info');
      return;
    }
    if (v.caseIds.length === 1) {
      this.showCaseDetail(v.caseIds[0]);
    } else {
      this.showCaseList(v.caseIds);
    }
  },

  /** 多案例时弹窗列出关联案例，点击进入详情 */
  showCaseList: function(caseIds) {
    var self = this;
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:640px;width:95%;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
      '<div style="padding:20px 24px;border-bottom:1px solid var(--color-border);display:flex;align-items:center;justify-content:space-between;">'+
        '<div><h3 style="margin:0;">关联案例 ('+caseIds.length+'个)</h3>'+
        '<div style="font-size:14px;color:var(--color-text-muted);margin-top:4px;">点击案例查看详情</div></div>'+
        '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="background:none;border:none;font-size:24px;cursor:pointer;color:var(--color-text-muted);">&times;</button></div>'+
      '<div style="padding:16px 24px;" id="case-list-body">加载中...</div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);

    var body = modal.querySelector('#case-list-body');
    Promise.all(caseIds.map(function(id){
      return fetch('/api/audit/cases/' + id).then(function(r){return r.json();}).then(function(d){
        return {id: id, title: (d.case && d.case.title) || ('案例 #' + id)};
      }).catch(function(){ return {id: id, title: '案例 #' + id}; });
    })).then(function(list){
      body.innerHTML = list.map(function(item){
        return '<div class="association-card" onclick="KnowledgeWorkshop.showCaseDetail('+item.id+');this.closest(\'[style*=fixed]\').remove();" style="cursor:pointer;">'+
          '<strong style="font-size:15px;">'+item.title+'</strong>'+
          '<div style="margin-top:4px;font-size:12px;color:var(--color-text-muted);"><i class="bi bi-arrow-right-circle"></i> 查看详情</div></div>';
      }).join('');
    }).catch(function(){
      body.innerHTML = '<div style="color:var(--color-text-muted);">加载失败</div>';
    });
  },

  /** 点击案例卡片 → 详情弹窗（案情摘要 + 关联违规 + 法规依据） */
  showCaseDetail: function(cid) {
    var self = this;
    var c = this.cases.find(function(x){ return String(x.id) === String(cid); });
    var title = c ? c.title : ('案例 #' + cid);
    var domain = c ? c.domain : '';
    var amount = c ? c.amount : '';
    var method = c ? c.method : '加载中...';
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:760px;width:95%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
      '<div style="padding:24px 28px;border-bottom:1px solid var(--color-border);display:flex;align-items:center;gap:12px;">'+
        '<div style="flex:1;"><h3 style="margin:0 0 4px;">'+title+'</h3>'+
        '<span class="badge badge-primary">'+domain+'</span> <span class="badge badge-muted">'+amount+'</span></div>'+
        '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="background:none;border:none;font-size:24px;cursor:pointer;color:var(--color-text-muted);">&times;</button></div>'+
      '<div style="padding:16px 28px;">'+
        '<div class="card" style="margin-bottom:12px;"><div class="card-header"><h4>案情摘要</h4></div>'+
        '<div style="padding:12px;font-size:14px;line-height:1.8;" id="case-summary-body">'+method+'</div></div>'+
        '<div class="card" style="margin-bottom:12px;"><div class="card-header"><h4>审计方法（核查手段）</h4></div>'+
        '<div style="padding:12px;font-size:13px;line-height:1.8;" id="case-method-body">加载中...</div></div>'+
        '<div class="card" style="margin-bottom:12px;"><div class="card-header"><h4>违规表现（审计发现）</h4></div>'+
        '<div style="padding:12px;font-size:13px;line-height:1.8;" id="case-finding-body">加载中...</div></div>'+
        '<div class="card" style="margin-bottom:12px;"><div class="card-header"><h4>风险影响</h4></div>'+
        '<div style="padding:12px;font-size:13px;line-height:1.8;" id="case-impact-body">加载中...</div></div>'+
        '<div class="card" style="margin-bottom:12px;"><div class="card-header"><h4>违规模型</h4></div>'+
        '<div style="padding:12px;font-size:13px;line-height:1.8;" id="case-violations">加载中...</div></div>'+
        '<div class="card" style="margin-bottom:12px;"><div class="card-header"><h4>法规依据</h4></div>'+
        '<div style="padding:12px;font-size:13px;line-height:1.8;" id="case-laws">加载中...</div></div>'+
        '<button class="btn btn-outline" style="width:100%;" onclick="this.closest(\'[style*=fixed]\').remove();">关闭</button>'+
      '</div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);

    fetch('/api/audit/cases/' + cid).then(function(r){return r.json();}).then(function(d){
      if(!d.success) return;
      // 非列表内案例时，用 API 数据补全标题/摘要
      if (!c && d.case) {
        var h3 = modal.querySelector('h3');
        var summaryBody = modal.querySelector('#case-summary-body');
        if (h3 && d.case.title) h3.textContent = d.case.title;
        if (summaryBody && d.case.case_summary) summaryBody.textContent = d.case.case_summary;
      }
      // 审计方法/违规表现/风险影响（来自案例详情）
      var methodBody = modal.querySelector('#case-method-body');
      var findingBody = modal.querySelector('#case-finding-body');
      var impactBody = modal.querySelector('#case-impact-body');
      if (d.case) {
        if (methodBody) methodBody.textContent = d.case.audit_method || '暂无';
        if (findingBody) findingBody.textContent = d.case.audit_finding || '暂无';
        if (impactBody) impactBody.textContent = d.case.audit_impact || '暂无';
      }
      var cv = modal.querySelector('#case-violations');
      var cl = modal.querySelector('#case-laws');
      if (d.violations && d.violations.length) {
        cv.innerHTML = d.violations.map(function(v){
          return '<div style="padding:4px 0;"><i class="bi bi-exclamation-triangle" style="color:var(--color-accent);"></i> '+
            (v.violation_title||'') + ' <span class="badge badge-muted">'+ (v.severity||'') +'</span></div>';
        }).join('');
      } else {
        cv.innerHTML = '<div style="color:var(--color-text-muted);">暂无关联违规</div>';
      }
      if (d.laws && d.laws.length) {
        cl.innerHTML = d.laws.map(function(l){
          var t = l.title || '';
          if (t && t.indexOf('《')!==0) t = '《' + t + '》';
          return '<div style="padding:4px 0;">'+ t + ' <span class="badge badge-muted">'+ (l.potency_level||'') +'</span></div>';
        }).join('');
      } else {
        cl.innerHTML = '<div style="color:var(--color-text-muted);">暂无关联法规</div>';
      }
    }).catch(function(){
      var cv = modal.querySelector('#case-violations');
      var cl = modal.querySelector('#case-laws');
      if (cv) cv.innerHTML = '<div style="color:var(--color-text-muted);">加载失败</div>';
      if (cl) cl.innerHTML = '';
    });
  },
};

document.addEventListener('DOMContentLoaded', function() {
  KnowledgeWorkshop.init();
  var hash = window.location.hash.replace('#', '');
  if (hash === 'regulations') KnowledgeWorkshop.switchTab('regulations');
  if (hash === 'cases') KnowledgeWorkshop.switchTab('cases');
  if (hash === 'violations') KnowledgeWorkshop.switchTab('violations');
});
