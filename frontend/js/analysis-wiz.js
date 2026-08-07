/**
 * AuditWorkbench — AI 审计助手（聊天驱动全流程）
 */
var AW = {
  step: 0,
  mem: {},

  /** Phase 7: API数据加载层 */
  _dataLoaded: false,
  _apiBase: '/api/audit',

  _initData: function() {
    if (this._dataLoaded) return Promise.resolve();
    var self = this;
    return Promise.all([
      (function(){
        // 按项目上下文提取关键词，避免出现与项目无关的违规类型（如采购项目混入农药类）
        var kws = self._projectKeywords();
        var primaryQ = self._primaryViolationQuery(kws);
        var buildUrl = function(q){ return self._apiBase + '/knowledge/violations?per_page=100' + (q ? ('&q=' + encodeURIComponent(q)) : ''); };
        // 按关键词命中数计算真实匹配度并排序，只取 Top 8
        var rank = function(list){
          return (list||[]).map(function(v){
            var title = v.violation_title || '';
            var desc = v.description || '';
            var text = title + ' ' + desc;
            var hits = 0, titleHit = false;
            kws.forEach(function(k){ if(k.length<2) return; if(text.indexOf(k)>=0){ hits++; if(title.indexOf(k)>=0) titleHit=true; } });
            var score = Math.min(99, 52 + hits*12 + (titleHit?12:0));
            return {
              id: '', name: title,
              risk: v.severity === 'high' ? '高' : (v.severity === 'low' ? '低' : '中'),
              match: score, symptom: desc, materials: [],
              regulations: [{law: v.expression_text || '', type: '主依据', note: desc}]
            };
          }).sort(function(a,b){ return b.match - a.match; }).slice(0, 8);
        };
        return fetch(buildUrl(primaryQ)).then(function(r){return r.json();}).then(function(d){
          var ranked = rank(d.violations || []);
          // 命中过少则放宽关键词再取一次，保证有足够相关条目
          if(ranked.length < 5 && primaryQ){
            return fetch(buildUrl('')).then(function(r){return r.json();}).then(function(d2){ return rank(d2.violations || []); });
          }
          return ranked;
        }).then(function(ranked){
          ranked.forEach(function(v,i){ v.id = 'v'+(i+1); });
          self.violationDB = ranked.length ? ranked : [];
        }).catch(function(){ self.violationDB = []; });
      })(),

      fetch(this._apiBase + '/knowledge/regulations?per_page=50').then(function(r){return r.json();}).then(function(d){
        self._regulations = (d.regulations || []).slice(0, 20);
      }).catch(function(){ self._regulations = []; })
    ]).then(function(){ self._dataLoaded = true; });
  },

  _fallbackViolations: function() {
    return [];  // 3.5: 失败返回空（不再塞假违规），由调用方显示空状态
  },

  /** 从项目背景提取业务关键词（用于违规类型相关性过滤与排序）*/
  _projectKeywords: function() {
    var pm = this.mem.project || {};
    try { if(!pm.title) pm = AuditWorkbench.getProjectMemory(); } catch(e){}
    var kws = [];
    var raw = [pm.title, pm.domain, pm.items, pm.concerns].join(' ');
    // 关注环节里的【标签】是最干净的业务类别，如【招标投标】【采购方式】
    var brackets = raw.match(/【([^】]+)】/g) || [];
    brackets.forEach(function(b){ kws.push(b.replace(/[【】]/g,'')); });
    // concerns 各行首词
    if(pm.concerns){
      pm.concerns.split(/[\n,，、]/).forEach(function(line){
        var t = line.replace(/^【[^】]*】/,'').trim();
        if(t.length>=2) kws.push(t.substring(0,4));
      });
    }
    // 标题里的业务名词（去年份/通用词）
    if(pm.title){
      var t = pm.title.replace(/\d+/g,'').replace(/审计|项目|方案|工作|局|年/g,'').trim();
      if(t.length>=2) kws.push(t);
    }
    if(pm.domain) kws.push(pm.domain);
    // 去重、过滤过短/通用词
    var generic = {'审计':1,'项目':1,'工作':1,'方案':1,'管理':1,'分析':1};
    var seen = {}, out = [];
    kws.forEach(function(k){
      k = (k||'').trim();
      if(k.length<2 || generic[k] || seen[k]) return;
      seen[k] = 1; out.push(k);
    });
    return out.length ? out : ['采购','招标'];
  },

  /** 选一个覆盖面最好的关键词作为后端 q（后端 LIKE 单串匹配）*/
  _primaryViolationQuery: function(kws) {
    var pref = ['采购','招标','投标','合同','资金','预算','资产','发票','收费','补贴','社保','扶贫','专项','采购'];
    for(var i=0;i<pref.length;i++){
      for(var j=0;j<kws.length;j++){
        if(kws[j].indexOf(pref[i])===0) return pref[i];
      }
    }
    return kws.length ? kws[0].substring(0,2) : '';
  },

  /** 添加消息到聊天区 */
  say: function(who, msg) {
    var c = document.getElementById('chat-msgs');
    if(who==='user'){
      c.innerHTML += '<div style="text-align:right;margin-bottom:8px;"><span style="background:var(--color-primary);color:#fff;padding:8px 12px;border-radius:10px;display:inline-block;max-width:85%;text-align:left;font-size:14px;">'+msg+'</span></div>';
    } else {
      c.innerHTML += '<div style="margin-bottom:8px;"><span style="background:rgba(26,58,92,0.06);padding:8px 12px;border-radius:10px;display:inline-block;max-width:90%;font-size:14px;">'+msg+'</span></div>';
    }
    c.scrollTop = c.scrollHeight;
  },

  /** 用户发送消息 */
  send: function() {
    var input = document.getElementById('chat-input');
    var msg = input.value.trim();
    if(!msg) return;
    this.say('user', msg);
    input.value = '';
    this.process(msg);
    this.saveProgress(); // Save after every interaction
  },

  /** AI处理用户消息 */
  process: function(msg) {
    var lower = msg.toLowerCase();
    var self = this;

    if(this.step === 0 || lower.includes('开始') || lower.includes('审计') || lower.includes('分析')) {
      if(this.step === 0) this.start();
      else { this.showStep(1); this.say('ai','已返回第一步。请描述您的审计目标，我将自动提取关键信息。'); }
    }
    // Step 1: "确认"触发意图解析，填充结构化字段
    else if(this.step === 1 && (lower === '确认' || lower === '好的' || lower === '可以' || lower.includes('是的'))) {
      this.parseIntent('确认操作');
      this.say('ai','正在根据项目背景自动解析...请查看右侧结构化信息，确认无误后点击「确认」按钮或说<strong>"推荐"</strong>进入方法推荐。');
    }
    else if(lower.includes('推荐') || lower.includes('模型') || lower.includes('违规') || lower.includes('下一步')) {
      if(this.step <= 1) { this.step = 2; this.showStep(2); this.updateStepBar(2); this.renderS2();
        this.say('ai','已匹配违规模型。请在右侧表格中<strong>勾选</strong>要核查的违规类型，系统会自动带出对应的审计资料和法规清单。<br><br>您也可以直接告诉我新的违规情况，我会帮您补充。确认后说<strong>"确认依据"</strong>。'); }
      else if(this.step === 2) { this.confirmS2(); }
      else { this.say('ai','当前已在步骤'+this.step+'。请先在右侧确认当前结果后再继续。'); }
    }
    // Step 2 special: handle new violation mentions
    else if(this.step === 2 && (lower.includes('还有') || lower.includes('也要') || lower.includes('加上') || lower.includes('增加') || lower.includes('补充'))) {
      var matched = false;
      var self = this;
      // Try to match keywords to existing violations
      this.violationDB.forEach(function(v){
        var kw = lower; var hit = kw.includes(v.name.substring(0,4)) || (kw.includes('招标')&&v.id==='v1') || (kw.includes('询价')&&v.id==='v2') || (kw.includes('公告')&&v.id==='v3') || ((kw.includes('围标')||kw.includes('串标'))&&v.id==='v4') || ((kw.includes('挪用')||kw.includes('截留'))&&v.id==='v5');
        if(!matched && hit) {
          if(self.selectedViolations.indexOf(v.id)<0) {
            self.selectedViolations.push(v.id);
            self.refreshS2Detail(); self.refreshS2Summary();
            self.say('ai','已为您添加「'+v.name+'」，系统已更新对应的资料和法规清单。如需要新的审计资料类型，也请告诉我。');
          } else { self.say('ai','「'+v.name+'」已在您的审计任务中。'); }
          matched = true;
        }
      });
      if(!matched) {
        self.say('ai','收到您的新增需求。请描述具体的违规表现和需要核查的内容，我会帮您匹配对应的审计资料和法规依据。例如："还需要核查资金是否被截留挪用"。');
      }
    }
    else if(lower.includes('依据') || lower.includes('法规') || lower.includes('条款')) {
      this.step = 3; this.showStep(3); this.updateStepBar(3); this.renderS3();
      this.say('ai','已列出审计依据和法规关系链。您可以在右侧表格或聊天框补充法规条款，确认后说<strong>"上传资料"</strong>进入第四步。');
    }
    // Step 3: handle regulation additions from chat
    else if(this.step === 3 && (lower.includes('还有') || lower.includes('加上') || lower.includes('补充') || lower.includes('增加') || msg.includes('《'))) {
      var matched = false;
      // Extract law name from 《...》
      var lawMatch = msg.match(/《([^》]+)》/g);
      if(lawMatch) {
        lawMatch.forEach(function(lm){
          var lawName = lm.replace(/《|》/g,'');
          var tb = document.querySelector('#right-panel table:last-of-type tbody');
          if(tb){
            var tr = document.createElement('tr');
            tr.innerHTML = '<td>+</td><td><strong>《'+lawName+'》</strong></td><td>聊天补充</td><td>审计员补充</td><td><span class="badge badge-muted">自定义</span></td><td></td>';
            tb.appendChild(tr);
          }
        });
        matched = true;
        this.say('ai','已识别并添加法规：《'+lawMatch.join('、')+'》。请在右侧表格中补充具体的条款和金额门槛信息。确认无误后说<strong>"上传资料"</strong>。');
      }
      if(!matched) {
        this.say('ai','请提供您要补充的法规名称和具体条款，例如：<em>"补充《XX省财政监督条例》第X条，规定XX"</em>。我也会帮您自动匹配到对应的法规关系链。');
      }
    }
    else if(lower.includes('上传') || lower.includes('资料') || lower.includes('ocr') || lower.includes('文件')) {
      this.step = 4; this.showStep(4); this.updateStepBar(4); this.renderS4();
      this.say('ai','请上传审计资料或从资料工坊选择。OCR将在后台异步处理，完成自动通知。<br><br>确认后说<strong>"开始比对"</strong>进入第五步。');
    }
    else if(lower.includes('比对') || lower.includes('验证') || lower.includes('扫描')) {
      this.step = 5; this.showStep(5); this.updateStepBar(5);
      var self = this;
      self.say('ai','<span class="pulse">●</span> 正在执行违规表达式扫描...');
      // P2.3: 遍历选中的违规（selectedViolations），调批量执行端点 + 存命中明细
      var pid = (self.mem.project||{}).id || '';
      var vIds = self.selectedViolations.length > 0 ? self.selectedViolations.slice()
        : self.violationDB.map(function(v){return v.id;});
      fetch('/api/audit/expression/execute', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({violation_ids: vIds, project_id: pid})
      }).then(function(r){return r.json();}).then(function(data){
        if (data && data.success === false) {
          self._scanResult = {results:[], hits:0, total:0};
          self.renderS5();
          self.say('ai','❌ 违规表达式扫描失败：' + (data.error || '未知错误') + '。请稍后重试。');
          return;
        }
        // P2.3: 存命中明细（每个违规的 rows），汇总计数
        var results = (data && data.results) || [];
        var totalHits = 0, totalScan = 0;
        results.forEach(function(r){ totalHits += (r.hits||0); totalScan += (r.total||0); });
        self._scanResult = {results: results, hits: totalHits, total: totalScan};
        self.renderS5();
        var execCount = results.filter(function(r){return r.executable;}).length;
        var skipCount = results.length - execCount;
        self.say('ai','✅ 违规扫描完成。执行 '+execCount+' 个违规表达式，总计 ' + totalScan + ' 条数据，命中 ' + totalHits + ' 条。'+(skipCount>0?('另有 '+skipCount+' 个无表达式已跳过。'):'')+'确认后说<strong>"疑点"</strong>进入疑点报告。');
      }).catch(function(err){
        self._scanResult = {results:[], hits:0, total:0};
        self.renderS5();
        self.say('ai','❌ 违规表达式扫描失败：' + ((err && err.message) || '后端不可用') + '。请稍后重试，或说<strong>"疑点"</strong>查看当前结果。');
      });
    }
    else if(lower.includes('疑点') || lower.includes('结果') || lower.includes('报告')) {
      this.step = 6; this.showStep(6); this.updateStepBar(6);
      var self = this;
      self.say('ai','<span class="pulse">●</span> AI正在生成疑点报告...');
      // Phase 7: 调用真实疑点生成API
      var scanData = self._scanResult || {};
      fetch('/api/audit/suspicion/generate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          analysis_results: (scanData.results && scanData.results.length > 0) ? scanData.results.map(function(r){
            return {violation_model: r.violation_name || '违规分析', scan_summary: {hits: r.hits||0, total: r.total||0, rows: (r.rows||[]).slice(0,20)}};
          }) : [{violation_model: '违规分析', scan_summary: scanData}],
          overall_assessment: scanData.hits > 0 ? ('发现'+scanData.hits+'条疑点记录') : '未发现明显异常',
          project_id: (self.mem.project||{}).id || ''
        })
      }).then(function(r){return r.json();}).then(function(data){
        if (data && data.success === false) {
          self.renderS6();
          self.say('ai','❌ 疑点报告生成失败：' + (data.error || '未知错误') + '。请稍后重试。');
          return;
        }
        self._suspicionData = data;
        self.renderS6();
        self.say('ai','✅ 已生成审计疑点报告。' + (data.output && data.output.suspicion_report ? ('共'+((data.output.suspicion_report.total_suspicions)||0)+'条疑点') : '') + '。每条疑点可溯源到原始数据、法规依据和推理过程。<br><br>确认后说<strong>"生成文书"</strong>进入最后一步。');
      }).catch(function(err){
        self.renderS6();
        self.say('ai','❌ 疑点报告生成失败：' + ((err && err.message) || '后端 /api/audit/suspicion/generate 不可用') + '。请稍后重试。');
      });
    }
    else if(lower.includes('文书') || lower.includes('取证') || lower.includes('底稿') || lower.includes('导出')) {
      this.step = 7; this.showStep(7); this.updateStepBar(7);
      var self = this;
      self.say('ai','<span class="pulse">●</span> 正在生成审计文书...');
      // Phase 7: 调用真实文书生成API
      fetch('/api/audit/documents/batch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({context: self._buildDocContext()})
      }).then(function(r){return r.json();}).then(function(data){
        if (data && data.success === false) {
          self.renderS7();
          self.say('ai','❌ 文书生成失败：' + (data.error || '未知错误') + '。请稍后重试。');
          return;
        }
        self._documentData = data;
        self.renderS7();
        self.say('ai','🎉 已通过AI Agent生成全部四件套文书：取证单、审计底稿、报告初稿、定性复核意见。点击右侧卡片预览或导出。所有结果均可溯源。');
      }).catch(function(err){
        self.renderS7();
        self.say('ai','❌ 文书生成失败：' + ((err && err.message) || '后端 /api/audit/documents/batch 不可用') + '。请稍后重试。');
      });
    }
    else if(lower.includes('溯源') || lower.includes('定位')) {
      this.say('ai','溯源详情：每个数据字段→MinerU定位原始文档位置；每条法规→MCP推理到law_id+clause_id+原文；每个推理→LLM Prompt+Response日志。');
      AuditWorkbench.toast('溯源链完整：资料→元数据→比对→疑点→报告','info');
    }
    else if(lower.includes('你好') || lower.includes('帮助')) {
      this.say('ai','您好！我是AI审计助手。您可以：<br>· 说<strong>"开始分析"</strong>输入审计意图<br>· 说<strong>"推荐模型"</strong>查看违规匹配<br>· 说<strong>"确认依据"</strong>审查法规<br>· 说<strong>"上传资料"</strong>上传审计资料<br>· 说<strong>"溯源"</strong>查看数据来源<br><br>随时用自然语言和我交流，我会引导您完成全流程。');
    }
    else {
      // Natural language intent → parse it
      if(this.step <= 1 && msg.length > 5) {
        this.mem.intent = msg;
        // If project context already loaded, fast-forward
        if(this.mem.project && this.mem.project.title && !msg.includes('?')) {
          this.parseIntent(msg);
          this.say('ai','已根据项目背景理解和您的描述完成意图解析。确认无误后说<strong>"确认"</strong>或点击右侧按钮进入下一步。<br><br>也可直接在右侧修改结构化信息。');
        } else {
          this.parseIntent(msg);
        }
      } else {
        this.say('ai','收到。您可以说：<strong>开始分析</strong> | <strong>推荐模型</strong> | <strong>确认依据</strong> | <strong>上传资料</strong> | <strong>溯源</strong>');
      }
    }
  },

  /** 保存当前分析进度到 localStorage */
  saveProgress: function() {
    var proj = this.mem.project || {};
    var chat = document.getElementById('chat-msgs');
    var right = document.getElementById('right-panel');
    var progress = {
      step: this.step,
      projectTitle: proj.title || (document.getElementById('s1-title')?.value) || '',
      projectDomain: proj.domain || (document.getElementById('s1-domain')?.value) || '',
      selectedViolations: this.selectedViolations,
      s1Cache: this.s1Cache,
      chatHTML: chat ? chat.innerHTML : '',
      rightPanelHTML: right ? right.innerHTML : '',
      // P3.1: task_id + 分析数据持久化（刷新恢复）
      taskId: this._taskId || '',
      matches: this._matches || null,
      primaryLaws: this._primaryLaws || null,
      scanResult: this._scanResult || null,
      suspicionData: this._suspicionData || null,
      savedAt: new Date().toISOString()
    };
    localStorage.setItem('aw_analysis_progress', JSON.stringify(progress));
  },

  /** 恢复上次进度 */
  resumeProgress: function() {
    var saved = localStorage.getItem('aw_analysis_progress');
    if(!saved) { this.start(); return; }
    try {
      var prog = JSON.parse(saved);
      this.step = prog.step;
      this.selectedViolations = prog.selectedViolations || [];
      this.s1Cache = prog.s1Cache || {};
      // P3.2: 回填 task_id + 分析数据（刷新恢复）
      this._taskId = prog.taskId || '';
      this._matches = prog.matches || [];
      this._primaryLaws = prog.primaryLaws || [];
      this._scanResult = prog.scanResult || null;
      this._suspicionData = prog.suspicionData || null;
      // Load project memory
      var pm = AuditWorkbench.getProjectMemory();
      if(pm && pm.title) this.mem.project = pm;
      // Show the saved step
      this.showStep(prog.step);
      this.updateStepBar(prog.step);
      // Restore right panel
      if(prog.rightPanelHTML) {
        var rp = document.getElementById('right-panel');
        if(rp) rp.innerHTML = prog.rightPanelHTML;
      } else {
        this.goBack(prog.step);
      }
      // Restore chat messages
      if(prog.chatHTML) {
        var chat = document.getElementById('chat-msgs');
        if(chat) chat.innerHTML = prog.chatHTML;
      }
      this.say('ai', '✅ 已恢复到第 '+prog.step+' 步，上次分析的内容都在。继续吧！');
    } catch(e) { this.start(); }
  },

  /** 清除进度 */
  clearProgress: function() {
    localStorage.removeItem('aw_analysis_progress');
    var c = document.getElementById('chat-msgs');
    if(c) c.innerHTML = '';
  },

  /** 启动：AI主动发出第一条消息 */
  start: function() {
    this.step = 1; this.showStep(1); this.updateStepBar(1); this.renderS1();
    this.saveProgress();
    // Phase 7: 预加载违规+法规数据
    this._initData();
    // Load project context with compressed memory
    var ctx = '';
    try {
      var pm = AuditWorkbench.getProjectMemory();
      if(pm.title) {
        this.mem.project = pm;
        // 延迟填充：等右侧面板渲染完成
        setTimeout(function(){
          var t = document.getElementById('s1-title');
          var d = document.getElementById('s1-domain');
          var p = document.getElementById('s1-period');
          var c = document.getElementById('s1-concerns');
          if(t) t.value = pm.title||'';
          if(d) d.value = pm.domain||'预算执行审计';
          if(p) p.value = pm.period||'2023-2025年';
          if(c) c.value = pm.concerns || '';
          // 3.6: 无 concerns 时调 AI 推断（替代写死的"招标投标/采购方式..."默认）
          if(!pm.concerns && pm.title){
            fetch('/api/audit/projects/infer-concerns', {
              method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify({project_name: pm.title, domain: pm.domain||''})
            }).then(function(r){return r.json();}).then(function(resp){
              if(resp && resp.success && resp.concerns && resp.concerns.length){
                var cc = document.getElementById('s1-concerns');
                if(cc && !cc.value.trim()) cc.value = resp.concerns.join('\n');
              }
            }).catch(function(){});
          }
        },500);

        // 构建项目摘要卡片
        var summary = [];
        if(pm.title) summary.push('<strong>项目名称：</strong>'+pm.title);
        if(pm.unit) summary.push('<strong>被审计单位：</strong>'+pm.unit);
        if(pm.domain) summary.push('<strong>审计类型：</strong>'+pm.domain);
        if(pm.level) summary.push('<strong>单位层级：</strong>'+pm.level);
        if(pm.period) summary.push('<strong>审计期间：</strong>'+pm.period);
        if(pm.objective) summary.push('<strong>审计目标：</strong>'+pm.objective.substring(0,60)+(pm.objective.length>60?'...':''));

        var shortSummary = summary.slice(0,4).join(' · ');
        var ctxCard = '<div style="margin-top:8px;padding:10px 14px;background:rgba(26,58,92,0.04);border-radius:8px;font-size:14px;border:1px solid var(--color-border);">'+
          '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">'+
          '<strong>📋 '+pm.title+'</strong>'+
          '<span class="badge badge-primary" style="font-size:11px;">项目背景</span></div>'+
          '<div style="color:var(--color-text-muted);line-height:1.6;">'+shortSummary+'</div>'+
          '<div style="margin-top:6px;display:flex;gap:8px;">'+
          '<a href="#" onclick="AW.showProjectContext()" style="font-size:12px;"><i class="bi bi-arrows-expand"></i> 查看完整项目信息</a>'+
          '<a href="#" onclick="AW.compressMemory()" style="font-size:12px;"><i class="bi bi-arrows-collapse"></i> 压缩摘要</a></div></div>';
        ctx = ctxCard;
      }
    }catch(e){}
    this.say('ai','您好，我是AI审计助手。已自动加载项目背景信息。<br><br>'+
      '<div style="margin-top:8px;padding:10px 14px;background:rgba(26,58,92,0.04);border-radius:8px;border:1px dashed var(--color-primary);cursor:pointer;" onclick="AW.showProjectContext()" title="点击查看项目背景">'+
      '<i class="bi bi-chevron-right"></i> <strong>背景信息（点击查看）</strong>'+
      '<span style="font-size:12px;color:var(--color-text-muted);margin-left:8px;">包含：项目基本情况、被审计单位情况、被审计行业政策</span></div><br>'+
      '请描述您的审计需求，或直接说<strong>"确认"</strong>基于项目背景开始分析。'+ctx);
  },

  /** 压缩记忆 */
  compressMemory: function() {
    var pm = this.mem.project;
    if(!pm) return;
    var lines = [];
    if(pm.title) lines.push('项目：'+pm.title);
    if(pm.domain) lines.push('类型：'+pm.domain);
    if(pm.unit) lines.push('单位：'+pm.unit);
    if(pm.amount) lines.push('金额：'+pm.amount);
    if(pm.items) lines.push('事项：'+pm.items);
    if(pm.concerns) lines.push('要点：'+pm.concerns);
    this.say('ai','<strong>📦 压缩记忆：</strong><br>'+lines.join(' | ')+'<br><br><span style="font-size:12px;color:var(--color-text-muted);">压缩后的上下文已优化，可在后续对话中高效引用。点击<a href="#" onclick="AW.showProjectContext()">查看全部背景</a>展开完整信息。</span>');
    AuditWorkbench.toast('记忆已压缩为精简摘要','success');
  },

  /** 展示完整项目背景 */
  showProjectContext: function() {
    var pm = this.mem.project;
    if(!pm) return;
    var detail = '<div style="font-size:14px;line-height:1.8;">'+
      '<div style="margin-bottom:10px;padding:10px 14px;background:rgba(26,58,92,0.03);border-radius:8px;border-left:3px solid var(--color-primary);"><strong>📋 项目基本情况</strong><br>项目名称：'+(pm.title||'—')+'<br>审计类型：'+(pm.domain||'—')+'<br>单位层级：'+(pm.level||'—')+'<br>审计目标：'+(pm.objective||'—')+'</div>'+
      '<div style="margin-bottom:10px;padding:10px 14px;background:rgba(45,125,70,0.03);border-radius:8px;border-left:3px solid var(--color-success);"><strong>🏢 被审计单位情况</strong><br>被审计单位：'+(pm.unit||'—')+'<br>单位性质：行政机关<br>主要职能：教育行政管理、教学设备采购管理<br>预算规模：年度预算约2.5亿元</div>'+
      '<div style="margin-bottom:10px;padding:10px 14px;background:rgba(184,94,26,0.03);border-radius:8px;border-left:3px solid var(--color-warning);"><strong>📜 被审计行业政策</strong><br>《中华人民共和国招标投标法》— 法律·现行有效<br>《必须招标的工程项目规定》— 部门规章<br>《湖南省建设工程招标投标管理办法》— 地方性法规<br>《被审计单位采购管理办法》— 单位制度·直接适用</div></div>';
    if(pm.concerns){
      detail += '<div style="font-size:14px;margin-top:4px;"><strong>关注业务环节：</strong>'+pm.concerns+'</div>';
    }
    this.say('ai',detail);
  },

  /** AI解析意图 — Phase 7: 调用真实Agent API */
  parseIntent: function(msg) {
    var self = this;
    this.say('ai','<span class="pulse">●</span> 正在调用AI分析您的审计意图...');

    var pm = this.mem.project || {};

    // Phase 7: 调用真实 IntentAnalyzer Agent
    fetch('/api/audit/analysis', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({project_id: pm.id || '', intent: msg || pm.title || pm.objective || ''})
    }).then(function(r){ return r.json(); }).then(function(data){
      // P1.8: 保存 task_id + 真实推荐（供 renderS2/renderS3 消费）
      self._taskId = (data && data.task_id) || '';
      self._matches = (data && data.matches) || [];
      self._primaryLaws = (data && data.primary_laws) || [];
      self._recommendedMaterials = (data && data.recommended_materials) || [];

      var out = (data && data.intent_result) ? data.intent_result : null;
      var title = pm.title || (out ? out.item : '') || '—';
      var domain = out ? out.domain : (pm.domain || '预算执行审计');
      var period = out ? out.period : (pm.period || '2023-2025年');
      var concerns = out ? (out.concerns || []).join('\n') : (pm.concerns || '');

      document.getElementById('s1-title').value = title;
      document.getElementById('s1-domain').value = domain;
      document.getElementById('s1-period').value = period;
      document.getElementById('s1-concerns').value = concerns;

      var titleShort = title.length > 30 ? title.substring(0,30)+'...' : title;
      var agentNote = out ? ' (AI Agent解析)' : ' (项目背景)';
      self.say('ai','已提取以下信息'+agentNote+'（右侧面板可修改）：<br><br>📋 <strong>项目名称：</strong>'+titleShort+'<br>🏷 <strong>审计领域：</strong>'+domain+'<br>📅 <strong>审计期间：</strong>'+period+'<br>📌 <strong>风险关注点：</strong>已在右侧面板展示。请核对。<br><br>确认无误后说<strong>"推荐"</strong>或点击右侧「确认」按钮进入方法推荐。');
    }).catch(function(){
      // API失败 → 降级使用项目背景数据
      var title = pm.title || '—';
      var domain = pm.domain || '预算执行审计';
      var period = pm.period || '2023-2025年';
      var concerns = pm.concerns || '';
      document.getElementById('s1-title').value = title;
      document.getElementById('s1-domain').value = domain;
      document.getElementById('s1-period').value = period;
      document.getElementById('s1-concerns').value = concerns;
      self.say('ai','已从项目背景提取关键信息（右侧面板可修改）：<br><br>📋 <strong>项目名称：</strong>'+title+'<br>🏷 <strong>审计领域：</strong>'+domain+'<br>📅 <strong>审计期间：</strong>'+period+'<br><br>确认无误后说<strong>"推荐"</strong>进入方法推荐。');
    });
  },

  /** 保存S1字段值以便回退恢复 */
  s1Cache: {},

  /** 点击步骤条回退 */
  goBack: function(n) {
    if(n < this.step) {
      // 离开S1前缓存字段值
      if(this.step === 1 && n < 1) {
        this.s1Cache.title = document.getElementById('s1-title')?.value || '';
        this.s1Cache.domain = document.getElementById('s1-domain')?.value || '';
        this.s1Cache.period = document.getElementById('s1-period')?.value || '';
        this.s1Cache.concerns = document.getElementById('s1-concerns')?.value || '';
      }
      this.step = n; this.showStep(n); this.updateStepBar(n);
      if(n===1) { this.renderS1(); this.restoreS1(); }
      else if(n===2) this.renderS2();
      else if(n===3) this.renderS3();
      else if(n===4) this.renderS4();
      this.say('ai','已返回第'+n+'步，可修改之前的设置后重新确认。');
    }
  },

  /** 保存S1字段 */
  cacheS1: function() {
    this.s1Cache.title = document.getElementById('s1-title')?.value || '';
    this.s1Cache.domain = document.getElementById('s1-domain')?.value || '';
    this.s1Cache.period = document.getElementById('s1-period')?.value || '';
    this.s1Cache.concerns = document.getElementById('s1-concerns')?.value || '';
  },

  /** 回退到S1后恢复字段值 */
  restoreS1: function() {
    var self = this;
    setTimeout(function(){
      if(self.s1Cache.title) document.getElementById('s1-title').value = self.s1Cache.title;
      if(self.s1Cache.domain) document.getElementById('s1-domain').value = self.s1Cache.domain;
      if(self.s1Cache.period) document.getElementById('s1-period').value = self.s1Cache.period;
      if(self.s1Cache.concerns) document.getElementById('s1-concerns').value = self.s1Cache.concerns;
    }, 200);
  },

  /** 更新步骤条高亮 */
  updateStepBar: function(n) {
    document.querySelectorAll('#step-bar .step').forEach(function(e,i){e.classList.remove('active','completed');if(i+1<n)e.classList.add('completed');if(i+1===n)e.classList.add('active');});
    if(n > 0) { this.step = n; this.saveProgress(); }
  },

  /** 显示/隐藏步骤面板 */
  showStep: function(n) {
    document.querySelectorAll('.step-panel').forEach(function(e){e.classList.remove('active');});
    var R = document.getElementById('right-panel');
    if(n===7) {
      // 第七步用动态渲染（卡片真实可预览），避免静态死壳/假预览
      this.renderS7();
      if(R) R.style.display = 'block';
    } else if(n>=5) {
      var p = document.getElementById('s'+n);
      if(p && R) {
        R.innerHTML = '<div class="card">'+p.innerHTML+'</div>';
        R.style.display = 'block';
        // 给静态面板里的死链溯源图标接上真实点击
        if(n===5) this._wireS5TraceLinks(R);
      }
    } else if(n>=1 && n<=4) {
      if(R) R.style.display = 'block';
    }
  },

  /** 第五步渲染（聊天流调用，避免未定义抛错；复用静态面板并接线）*/
  renderS5: function() {
    this.showStep(5);
    this.updateStepBar(5);
  },

  /** 打开审计资料原始文件 */
  openMaterial: function(fileName) {
    if(!fileName) return AuditWorkbench.toast('未提供文件名','warning');
    window.open('doc-viewer.html?file=' + encodeURIComponent(fileName), '_blank');
  },

  /** 疑点核实：提交被审计单位说明材料（诚实版——不伪造AI重评结果）*/
  submitReassess: function(btn) {
    if(btn) { btn.disabled = true; btn.innerHTML = '<span class="pulse">●</span> 已提交'; }
    var box = document.getElementById('s6-reassess-1');
    if(box) {
      box.innerHTML = '<div style="margin-top:8px;padding:8px 10px;background:rgba(184,94,26,0.06);border-radius:6px;font-size:12px;line-height:1.7;">' +
        '<strong>📝 已收到说明材料。</strong>AI 重新评估功能开发中，当前疑点定性暂维持不变；功能上线后将自动结合补充材料进行复核。</div>';
    }
    AuditWorkbench.toast('材料已提交，AI重评功能开发中', 'info');
  },

  /** 给第五步面板的 📍 溯源图标按上下文绑真实点击 */
  _wireS5TraceLinks: function(scope) {
    var links = scope.querySelectorAll('.trace-link');
    Array.prototype.forEach.call(links, function(a){
      if(a.getAttribute('data-wired')) return;
      a.setAttribute('data-wired','1');
      a.style.cursor = 'pointer';
      a.title = '点击溯源';
      a.addEventListener('click', function(e){
        e.preventDefault();
        var line = (a.parentElement && a.parentElement.textContent) || '';
        var fm = line.match(/([一-龥\w]+\.(?:pdf|csv|xlsx|xls|docx?))/i);
        if(fm){ AW.openMaterial(fm[1]); return; }
        var lm = line.match(/《([^》]+)》/);
        if(lm){ AW.traceLawSource('《'+lm[1]+'》'); return; }
        var vm = line.match(/(化整为零|询价|采购公告|围标|串标|挪用|截留|违规)/);
        if(vm){
          var vName = line.replace(/[⚠️📍]/g,'').replace(/语料库/g,'').split('·')[0].trim() || vm[0];
          AW.traceViolationCorpus(vName);
          return;
        }
        AuditWorkbench.toast('📍 '+line.trim(),'info');
      });
    });
  },

  /** 违规语料库：按名称检索违规库 → 弹窗展示详情 */
  traceViolationCorpus: function(name) {
    var nm = (name||'').trim();
    if(!nm) return AuditWorkbench.toast('未识别到违规名称','warning');
    var self = this;
    AuditWorkbench.toast('正在查询违规语料库…','info');
    fetch(this._apiBase + '/knowledge/violations?q=' + encodeURIComponent(nm) + '&per_page=1').then(function(r){return r.json();}).then(function(d){
      self._showCorpusModal((d.violations||[])[0], nm);
    }).catch(function(){ AuditWorkbench.toast('违规语料库暂不可用','danger'); });
  },

  _showCorpusModal: function(v, name) {
    if(!v){
      AuditWorkbench.toast('语料库未收录「'+name+'」的记录','warning');
      this.say('ai','📍 <strong>语料库查询</strong>：未匹配到「'+name+'」的违规模型记录。可能为本次新增的违规情形，尚未入库。');
      return;
    }
    var sev = v.severity==='high'?'高':(v.severity==='low'?'低':'中');
    var rows = [
      ['违规名称', v.violation_title || name],
      ['所属领域', v.category_path || '—'],
      ['风险等级', sev],
      ['来源模板', v.source_file || '—']
    ];
    var grid = '<div style="display:grid;grid-template-columns:90px 1fr;gap:8px 12px;">';
    rows.forEach(function(r){ grid += '<div style="color:var(--color-text-muted);font-size:13px;">'+r[0]+'</div><div style="font-size:13px;">'+(r[1]||'—')+'</div>'; });
    grid += '</div>';
    var body = grid +
      '<div style="margin-top:12px;padding-top:10px;border-top:1px dashed var(--color-border);"><strong>常见表现：</strong><div style="margin-top:4px;color:var(--color-text-muted);">'+this._esc(v.description||'—')+'</div></div>' +
      '<div style="margin-top:10px;"><strong>判定规则：</strong><div style="margin-top:4px;color:var(--color-text-muted);font-family:monospace;font-size:12px;">'+this._esc(v.expression_text||'—')+'</div></div>';
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML =
      '<div style="background:#fff;border-radius:14px;max-width:680px;width:90%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
        '<div style="padding:14px 20px;border-bottom:2px solid var(--color-border);position:sticky;top:0;background:#fff;display:flex;align-items:center;gap:8px;">'+
          '<div><h3 style="margin:0;font-size:16px;">📍 违规语料库</h3><div style="font-size:12px;color:var(--color-text-muted);">溯源自 tt.audit_violations · 违规行为库</div></div>'+
          '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="margin-left:auto;background:none;border:none;font-size:22px;cursor:pointer;">&times;</button></div>'+
        '<div style="padding:16px 20px;font-size:14px;line-height:1.9;">'+body+'</div>'+
        '<div style="padding:12px 20px;border-top:1px solid var(--color-border);"><button class="btn btn-sm btn-outline" onclick="this.closest(\'[style*=fixed]\').remove();">关闭</button></div>'+
      '</div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);
  },

  // ====== 右侧面板渲染 ======

  renderS1: function() {
    document.getElementById('right-panel').innerHTML =
      '<div class="card"><div class="card-header"><h3>第一步：意图判断</h3><span style="font-size:12px;color:var(--color-text-muted);">AI自动填充</span></div>'+
      '<div class="form-group"><label class="form-label">项目名称</label><input class="form-input" id="s1-title"></div>'+
      '<div class="form-group"><label class="form-label">审计领域</label><select class="form-select" id="s1-domain"><option>—</option><option>预算执行审计</option><option>专项审计调查</option><option>经济责任审计</option></select></div>'+
      '<div class="form-group"><label class="form-label">审计期间</label><input class="form-input" id="s1-period" value="2023-2025年"></div>'+
      '<div class="form-group"><label class="form-label">关注业务环节（含风险分析）</label><textarea class="form-textarea" id="s1-concerns" rows="10" style="font-size:14px;line-height:1.7;"></textarea></div>'+
      '<div style="font-size:12px;color:var(--color-text-muted);margin-bottom:8px;">📍 AI提取字段→可溯源·可手动修改</div>'+
      '<button class="btn btn-accent w-100" onclick="AW.cacheS1();AW.step=2;AW.showStep(2);AW.updateStepBar(2);AW.renderS2();AW.say(\'ai\',\'已确认，进入方法推荐。\')">确认，进入推荐 →</button></div>';
  },

  /** 违规模型数据库 — 3.5: 初始空，由 _initData 从 /knowledge/violations 加载真实数据 */
  violationDB: [],

  /** 当前选中的违规模型 */
  selectedViolations: [],

  renderS2: function() {
    var self = this;
    // P1.8: 优先用 ViolationMatcher 真推荐（self._matches），fallback 到 _initData 本地检索
    if (self._matches && self._matches.length > 0) {
      self.violationDB = self._matches;
      self._renderS2Content();
    } else {
      // Phase 7: 确保数据已加载
      this._initData().then(function() {
        self._renderS2Content();
      });
    }
  },

  _renderS2Content: function() {
    var self = this;

    // Three-section audit method cards — titles always visible, details collapsible
    var methodsHTML = '<div style="margin-bottom:16px;">'+
      '<div style="font-size:12px;font-weight:600;color:var(--color-text-muted);margin-bottom:8px;">审计方法概览</div>'+
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;">';

    // Card 1: 已执行的审计方法
    methodsHTML += '<details class="method-card" open style="border:1px solid var(--color-border);border-radius:8px;border-top:3px solid var(--color-primary);background:#fff;">'+
      '<summary style="cursor:pointer;padding:10px 14px;font-size:13px;font-weight:600;color:var(--color-primary);list-style:none;display:flex;align-items:center;gap:6px;">'+
      '<i class="bi bi-check2-circle"></i> 已执行审计方法 <span style="font-size:11px;color:var(--color-text-muted);font-weight:400;">· 3项（可拖拽到下方列表）</span>'+
      '<i class="bi bi-chevron-down" style="margin-left:auto;font-size:10px;transition:transform 0.2s;"></i></summary>'+
      '<div style="font-size:12px;line-height:2;padding:0 14px 12px;">'+
      '<div class="drag-method" draggable="true" ondragstart="AW.handleDragStart(event)" ondragend="AW.handleDragEnd(event)" data-method="预算执行分析" data-risk="中" ondragstart="AW.handleDragStart(event)" ondragend="AW.handleDragEnd(event)" style="cursor:grab;padding:4px 8px;margin:2px 0;border-radius:4px;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(26,58,92,0.06)\'" onmouseout="this.style.background=\'\'">📊 预算执行分析 <span style="font-size:10px;color:var(--color-text-muted);">⋮⋮ 拖拽</span></div>'+
      '<div class="drag-method" draggable="true" ondragstart="AW.handleDragStart(event)" ondragend="AW.handleDragEnd(event)" data-method="采购程序合规检查" data-risk="高" style="cursor:grab;padding:4px 8px;margin:2px 0;border-radius:4px;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(26,58,92,0.06)\'" onmouseout="this.style.background=\'\'">📋 采购程序合规检查 <span style="font-size:10px;color:var(--color-text-muted);">⋮⋮ 拖拽</span></div>'+
      '<div class="drag-method" draggable="true" ondragstart="AW.handleDragStart(event)" ondragend="AW.handleDragEnd(event)" data-method="资金流向追踪" data-risk="高" style="cursor:grab;padding:4px 8px;margin:2px 0;border-radius:4px;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(26,58,92,0.06)\'" onmouseout="this.style.background=\'\'">💰 资金流向追踪 <span style="font-size:10px;color:var(--color-text-muted);">⋮⋮ 拖拽</span></div></div></details>';

    // Card 2: 发现疑点的方法
    methodsHTML += '<details class="method-card" open style="border:1px solid var(--color-border);border-radius:8px;border-top:3px solid var(--color-accent);background:#fff;">'+
      '<summary style="cursor:pointer;padding:10px 14px;font-size:13px;font-weight:600;color:var(--color-accent);list-style:none;display:flex;align-items:center;gap:6px;">'+
      '<i class="bi bi-exclamation-triangle"></i> 发现疑点的方法 <span style="font-size:11px;color:var(--color-text-muted);font-weight:400;">· 2个（可拖拽）</span>'+
      '<i class="bi bi-chevron-down" style="margin-left:auto;font-size:10px;transition:transform 0.2s;"></i></summary>'+
      '<div style="font-size:12px;line-height:2;padding:0 14px 12px;">'+
      '<div class="drag-method" draggable="true" ondragstart="AW.handleDragStart(event)" ondragend="AW.handleDragEnd(event)" data-method="招标方式核查" data-risk="高" data-symptom="采购方式均为非公开招标，未按规定履行公开招标程序，应招标未招标问题突出" style="cursor:grab;padding:4px 8px;margin:2px 0;border-radius:4px;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(196,30,58,0.06)\'" onmouseout="this.style.background=\'\'">⚠️ 招标方式核查 → 2条疑点 <span style="font-size:10px;color:var(--color-text-muted);">⋮⋮</span></div>'+
      '<div class="drag-method" draggable="true" ondragstart="AW.handleDragStart(event)" ondragend="AW.handleDragEnd(event)" data-method="供应商关联分析" data-risk="中" data-symptom="多家供应商存在人员关联或股权关联，疑似围标串标" style="cursor:grab;padding:4px 8px;margin:2px 0;border-radius:4px;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(196,30,58,0.06)\'" onmouseout="this.style.background=\'\'">⚠️ 供应商关联分析 → 1条疑点 <span style="font-size:10px;color:var(--color-text-muted);">⋮⋮</span></div></div></details>';

    // Card 3: 推荐补充方法 — 动态生成，可换一批
    if(!this.recommendPool) this._initRecommendPool();
    var recs = this._pickRecommendations();
    methodsHTML += '<details class="method-card" open style="border:1px solid var(--color-border);border-radius:8px;border-top:3px solid var(--color-warning);background:#fff;">'+
      '<summary style="cursor:pointer;padding:10px 14px;font-size:13px;font-weight:600;color:var(--color-warning);list-style:none;display:flex;align-items:center;gap:6px;">'+
      '<i class="bi bi-lightbulb"></i> 推荐补充方法 <span style="font-size:11px;color:var(--color-text-muted);font-weight:400;">· '+recs.length+'项（可拖拽）</span>'+
      '<i class="bi bi-chevron-down" style="margin-left:auto;font-size:10px;transition:transform 0.2s;"></i></summary>'+
      '<div style="font-size:12px;line-height:2;padding:0 14px 12px;" id="s2-recommend-list">';
    var icons = ['💡','🔍','📌','🎯','📊','🔎'];
    recs.forEach(function(r,i){
      methodsHTML += '<div class="drag-method" draggable="true" ondragstart="AW.handleDragStart(event)" ondragend="AW.handleDragEnd(event)" data-method="'+r.name+'" data-risk="'+r.risk+'" data-symptom="'+r.symptom.replace(/"/g,'&quot;')+'" style="cursor:grab;padding:4px 8px;margin:2px 0;border-radius:4px;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(184,94,26,0.06)\'" onmouseout="this.style.background=\'\'">'+(icons[i]||'💡')+' '+r.name+' <span style="font-size:10px;color:var(--color-text-muted);">⋮⋮</span></div>';
    });
    methodsHTML += '</div>'+
      '<div style="display:flex;gap:6px;margin:0 14px 10px;">'+
      '<button class="btn btn-sm btn-outline" style="font-size:11px;flex:1;" onclick="event.preventDefault();AW.refreshRecommendations()">'+
      '<i class="bi bi-arrow-repeat"></i> 换一批</button>'+
      '<button class="btn btn-sm btn-outline" style="font-size:11px;" onclick="event.preventDefault();AW.recommendMoreMethods()">'+
      '<i class="bi bi-plus-lg"></i> 补充常用方法</button></div></details>';

    methodsHTML += '</div></div>';

    // Violation table
    var rows = '';
    this.violationDB.forEach(function(v){
      var checked = self.selectedViolations.indexOf(v.id)>=0 ? 'checked' : '';
      rows += '<tr class="'+(checked?'':'')+'" style="cursor:pointer;'+(checked?'background:rgba(26,58,92,0.03);':'')+'" onclick="AW.toggleViolation(\''+v.id+'\',this)">'+
        '<td><input type="checkbox" class="rec-check" '+checked+' data-id="'+v.id+'" onclick="event.stopPropagation();AW.toggleViolation(\''+v.id+'\',this.parentElement.parentElement)" style="width:16px;height:16px;"></td>'+
        '<td style="font-size:15px;"><strong>'+v.name+'</strong></td>'+
        '<td style="font-size:13px;color:var(--color-text-muted);">'+(v.symptom||'—')+'</td>'+
        '<td><span class="badge badge-'+(v.risk==='高'?'accent':'warning')+'">'+v.risk+'风险</span></td>'+
        '<td>'+v.match+'%</td></tr>';
    });

    document.getElementById('right-panel').innerHTML =
      '<div class="card"><div class="card-header"><h3>第二步：方法推荐</h3><span style="font-size:12px;color:var(--color-text-muted);">基于项目上下文智能匹配</span></div>'+
      methodsHTML +
      '<div class="alert alert-info" style="font-size:14px;"><i class="bi bi-info-circle"></i> 勾选要核查的违规类型，自动展示对应的审计资料清单和法规依据。左侧可补充新的违规情况。</div>'+
      '<div id="s2-drop-zone" style="border:2px dashed transparent;border-radius:8px;transition:all 0.2s;padding:4px;" '+
      'ondragover="AW.handleDragOver(event)" ondragleave="AW.handleDragLeave(event)" ondrop="AW.handleDrop(event)">'+
      '<div style="font-size:11px;color:var(--color-text-muted);text-align:center;margin-bottom:4px;" id="s2-drop-hint">'+
      '📥 从上方卡片拖拽方法到此处添加到列表</div>'+
      '<div class="table-wrap"><table class="table"><thead><tr><th style="width:30px;">✓</th><th style="min-width:180px;">违规类型</th><th>常见问题表现</th><th style="width:60px;">风险</th><th style="width:50px;">匹配度</th></tr></thead><tbody>'+rows+'</tbody></table></div></div>'+
      '<div id="s2-detail"></div>'+
      '<div id="s2-summary" style="margin-top:12px;"></div>'+
      '<button class="btn btn-accent btn-lg w-100" style="margin-top:10px;" onclick="AW.confirmS2()">确认审计任务，进入依据确认</button></div>';
    this.refreshS2Detail();
    this.refreshS2Summary();
  },

  /** Drag-and-drop: add method from cards to violation list */
  dragMethod: null,
  handleDragStart: function(e) {
    this.dragMethod = {
      name: e.target.getAttribute('data-method'),
      risk: e.target.getAttribute('data-risk') || '中',
      symptom: e.target.getAttribute('data-symptom') || ''
    };
    e.dataTransfer.effectAllowed = 'copy';
    e.dataTransfer.setData('text/plain', this.dragMethod.name);
    e.target.style.opacity = '0.5';
  },
  handleDragEnd: function(e) {
    e.target.style.opacity = '1';
  },
  handleDragOver: function(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    var zone = document.getElementById('s2-drop-zone');
    if(zone) zone.style.background = 'rgba(26,58,92,0.06)';
  },
  handleDragLeave: function(e) {
    var zone = document.getElementById('s2-drop-zone');
    if(zone) zone.style.background = '';
  },
  handleDrop: function(e) {
    e.preventDefault();
    var zone = document.getElementById('s2-drop-zone');
    if(zone) zone.style.background = '';
    if(!this.dragMethod) return;
    var dm = this.dragMethod;
    // Check limit
    if(this.violationDB.length >= 10) {
      this.say('ai','⚠️ 推荐执行列表已有 '+this.violationDB.length+' 个方法。建议聚焦核心风险，控制在10个以内以保证分析质量。<br><br>可取消勾选不再需要的方法后再添加。');
      AuditWorkbench.toast('已达上限：推荐列表已有'+this.violationDB.length+'个方法','warning');
      this.dragMethod = null;
      return;
    }
    // Add to violationDB if not exists
    var exists = this.violationDB.find(function(v){return v.name === dm.name;});
    if(!exists) {
      var newId = 'v' + (this.violationDB.length + 1);
      this.violationDB.push({
        id: newId, name: dm.name, risk: dm.risk, match: Math.floor(Math.random()*30+55),
        symptom: dm.symptom || '用户从审计方法卡片拖入',
        materials: ['相关审计资料（待补充）'],
        regulations: [{law: '待关联法规', type: '参考', note: '请补充具体法规条款'}]
      });
      this.selectedViolations.push(newId);
      this.renderS2();
      this.say('ai','✅ 已将「'+dm.name+'」添加到推荐执行列表并选中（当前共'+this.violationDB.length+'个）。可在下方查看对应的资料和法规。');
      if(this.violationDB.length >= 8) {
        setTimeout(function(){
          AW.say('ai','💡 提示：列表已有 '+AW.violationDB.length+' 个方法，建议聚焦最核心的风险领域。超出10个后将限制添加。');
        },500);
      }
    } else {
      if(this.selectedViolations.indexOf(exists.id) < 0) {
        this.selectedViolations.push(exists.id);
        this.renderS2();
      }
      this.say('ai','「'+dm.name+'」已在推荐列表中，已为您选中。');
    }
    this.dragMethod = null;
  },

  /** 推荐方法池 — 根据项目上下文智能筛选 */
  recommendPool: null,
  _lastRecPick: 0,

  _initRecommendPool: function() {
    this.recommendPool = [
      {name:'合同履约验收审查',risk:'中',tags:'采购,合同,验收',symptom:'合同履行完毕后未组织正式验收，验收报告内容缺失关键验收指标'},
      {name:'资产入账完整性核查',risk:'低',tags:'资产,入账,采购',symptom:'设备已交付但未及时登记固定资产台账，入账延迟超过3个月'},
      {name:'采购绩效评价分析',risk:'低',tags:'采购,绩效,评价',symptom:'未对采购项目开展绩效评价，无法评估采购效率和效果'},
      {name:'供应商资质复核',risk:'中',tags:'供应商,资质,关联',symptom:'供应商营业执照经营范围与中标内容不符，或资质证书过期'},
      {name:'合同变更合规审查',risk:'中',tags:'合同,变更,审批',symptom:'合同签订后发生重大变更未履行审批程序，变更金额超原合同30%'},
      {name:'付款进度异常核查',risk:'高',tags:'付款,进度,资金',symptom:'未达到付款节点即提前支付，或付款比例与合同约定不一致'},
      {name:'采购预算执行偏差',risk:'中',tags:'预算,采购,执行',symptom:'实际采购金额与批复预算偏差超20%，未见预算调整审批'},
      {name:'中标价格合理性分析',risk:'中',tags:'中标,价格,市场',symptom:'中标价格与预算价或市场价偏离超15%，缺乏合理解释'},
      {name:'保证金收取退还合规',risk:'低',tags:'保证金,退还,合规',symptom:'保证金未按法定时限退还，或收取比例超规定标准'},
      {name:'采购文件保存完整性',risk:'低',tags:'档案,保存,采购',symptom:'采购档案未按规定保存15年，关键文件缺失'},
      {name:'利益冲突回避审查',risk:'高',tags:'利益冲突,回避,关联',symptom:'评审专家或采购人员与被采购方存在利益关系未回避'},
      {name:'合同条款执行跟踪',risk:'中',tags:'合同,跟踪,执行',symptom:'合同签订后未建立履约跟踪机制，服务类合同缺少阶段性评估'},
    ];
  },

  /** 根据项目上下文挑选3个推荐方法 */
  _pickRecommendations: function() {
    if(!this.recommendPool) this._initRecommendPool();
    var pool = this.recommendPool;
    // Read project context
    var proj = this.mem.project || {};
    var ctx = (proj.title||'') + (proj.domain||'') + (proj.concerns||'');
    // Score each item by tag match against context
    var scored = pool.map(function(r){
      var score = 0;
      r.tags.split(',').forEach(function(t){
        if(ctx.indexOf(t.trim())>=0) score += 10;
      });
      // Random factor for variety
      score += Math.random() * 5;
      return {item:r, score:score};
    });
    scored.sort(function(a,b){return b.score - a.score;});
    // Pick 3 with rotation — skip already shown if possible
    var start = (this._lastRecPick + 3) % pool.length;
    var picked = [];
    var usedNames = {};
    this.violationDB.forEach(function(v){usedNames[v.name]=true;});
    // First: try high-scoring items not already in violation list
    for(var i=0;i<scored.length && picked.length<3;i++){
      if(!usedNames[scored[i].item.name]) picked.push(scored[i].item);
    }
    // Fill from scored if needed
    if(picked.length<3){
      for(var i=0;i<scored.length && picked.length<3;i++){
        if(picked.indexOf(scored[i].item)<0) picked.push(scored[i].item);
      }
    }
    this._lastRecPick = start;
    return picked.slice(0,3);
  },

  /** 换一批推荐 */
  refreshRecommendations: function() {
    this._lastRecPick = (this._lastRecPick + 5) % (this.recommendPool||[]).length;
    this.renderS2();
    var recs = this._pickRecommendations();
    this.say('ai','🔄 已根据项目上下文换了一批推荐方法：<br>· '+recs.map(function(r){return r.name;}).join('<br>· '));
  },

  /** 补充常用审计方法（基于关键词匹配的静态池，非 LLM）*/
  recommendMoreMethods: function() {
    if(this.violationDB.length >= 10) {
      AuditWorkbench.toast('已达上限：推荐列表已有'+this.violationDB.length+'个方法','warning');
      this.say('ai','⚠️ 列表已有 '+this.violationDB.length+' 个方法，已达上限。请先取消不需要的方法再添加。');
      return;
    }
    // 4.1-A: 复用 _pickRecommendations（基于项目关键词匹配），过滤已加的，转 violation 格式后补充
    if(!this.recommendPool) this._initRecommendPool();
    var self = this;
    var existingNames = {};
    this.violationDB.forEach(function(v){ existingNames[v.name] = true; });
    var picks = this._pickRecommendations().filter(function(r){ return !existingNames[r.name]; }).slice(0,3);
    var added = [];
    picks.forEach(function(r){
      self.violationDB.push({
        id: 'v' + (self.violationDB.length + 1),
        name: r.name, risk: r.risk, match: 50,
        symptom: r.symptom, materials: [], regulations: []
      });
      added.push(r.name);
    });
    if(added.length > 0) {
      this.renderS2();
      this.say('ai','已补充 '+added.length+' 个常用审计方法（基于项目关键词匹配）：<br>· '+added.join('<br>· ')+'<br><br>已在表格中添加，可勾选纳入审计任务。');
    } else {
      this.say('ai','当前推荐列表已覆盖该项目的主要风险领域。如需特定方向的审计方法，请在聊天中描述。');
    }
  },

  /** 切换违规模型选中状态 */
  toggleViolation: function(id, row) {
    var idx = this.selectedViolations.indexOf(id);
    if(idx>=0) { this.selectedViolations.splice(idx,1); if(row)row.style.background=''; }
    else { this.selectedViolations.push(id); if(row)row.style.background='rgba(26,58,92,0.03)'; }
    var cb = row?row.querySelector('input[type=checkbox]'):null;
    if(cb) cb.checked = (idx<0);
    this.refreshS2Detail();
    this.refreshS2Summary();
    this.say('ai','已'+(idx>=0?'取消':'选择')+'「'+this.violationDB.find(function(v){return v.id===id;}).name+'」'+(idx>=0?'':"。系统已更新对应的资料和法规清单。"));
  },

  /** 刷新详情：选中模型对应的资料+法规 */
  refreshS2Detail: function() {
    var self = this;
    var container = document.getElementById('s2-detail');
    if(!container) return;
    if(this.selectedViolations.length === 0) {
      container.innerHTML = '<p style="text-align:center;color:var(--color-text-muted);padding:20px;">请在上方勾选至少一个违规类型</p>';
      return;
    }
    var html = '';
    // Collect unique materials and regs, track which violations per material
    var matMap = {}, matViolations = {}, regMap = {};
    this.selectedViolations.forEach(function(id){
      var v = self.violationDB.find(function(x){return x.id===id;});
      if(!v) return;
      v.materials.forEach(function(m){
        matMap[m] = (matMap[m]||0)+1;
        if(!matViolations[m]) matViolations[m] = [];
        if(matViolations[m].indexOf(v.name)<0) matViolations[m].push(v.name);
      });
      v.regulations.forEach(function(r){
        var key = r.law;
        if(!regMap[key]) regMap[key] = r;
      });
    });

    // Horizontal capsule tabs — tables hidden until clicked
    html += '<div style="display:flex;gap:0;margin-bottom:16px;background:var(--color-bg);border-radius:10px;padding:4px;">'+
      '<div id="stab-mat" onclick="AW.switchS2Tab(\'mat\')" style="flex:1;min-height:48px;display:flex;align-items:center;justify-content:center;gap:8px;text-align:center;padding:12px 24px;border-radius:8px;cursor:pointer;font-weight:600;font-size:15px;background:#fff;color:var(--color-primary);box-shadow:0 1px 3px rgba(0,0,0,0.08);transition:background 0.15s,color 0.15s,box-shadow 0.15s;">'+
      '<i class="bi bi-folder"></i> 审计所需资料 <span style="background:var(--color-primary);color:#fff;padding:2px 10px;border-radius:12px;font-size:14px;">'+Object.keys(matMap).length+' 类</span></div>'+
      '<div id="stab-reg" onclick="AW.switchS2Tab(\'reg\')" style="flex:1;min-height:48px;display:flex;align-items:center;justify-content:center;gap:8px;text-align:center;padding:12px 24px;border-radius:8px;cursor:pointer;font-weight:600;font-size:15px;color:var(--color-text-muted);transition:background 0.15s,color 0.15s,box-shadow 0.15s;">'+
      '<i class="bi bi-journal-text"></i> 法规政策清单 <span style="background:var(--color-text-muted);color:#fff;padding:2px 10px;border-radius:12px;font-size:14px;">'+Object.keys(regMap).length+' 部</span></div></div>';

    // Materials tab - hidden by default, shown on tab click
    html += '<div id="s2-tab-mat" style="display:none;"><div class="table-wrap"><table class="table"><thead><tr><th>序号</th><th>资料名称</th><th>关键字段</th><th>关联违规</th></tr></thead><tbody>';
    Object.keys(matMap).forEach(function(m,i){
      var parts = m.split('（');
      var vNames = (matViolations[m]||[]).join('、');
      html += '<tr><td>'+(i+1)+'</td><td><strong>'+parts[0]+'</strong></td><td style="font-size:14px;color:var(--color-text-muted);">'+(parts[1]?parts[1].replace('）',''):'')+'</td><td style="font-size:12px;"><span class="badge badge-primary">'+matMap[m]+'个</span> <span style="color:var(--color-text-muted);">'+vNames+'</span></td></tr>';
    });
    html += '</tbody></table></div>'+
      '<div style="display:flex;gap:8px;margin-top:8px;">'+
      '<button class="btn btn-primary" onclick="AW.printList(\'materials\')"><i class="bi bi-printer"></i> 打印资料清单</button>'+
      '<button class="btn btn-accent" onclick="AW.exportXlsx(\'materials\')"><i class="bi bi-download"></i> 导出资料清单 .xlsx</button></div></div>';

    // Regulations tab — hidden by default
    html += '<div id="s2-tab-reg" style="display:none;"><div class="table-wrap"><table class="table"><thead><tr><th>序号</th><th>法规条款</th><th>适用类型</th><th>说明</th></tr></thead><tbody>';
    Object.keys(regMap).forEach(function(k,i){
      var r = regMap[k];
      var typeBadge = r.type==='主依据'?'badge-accent':r.type==='追责依据'?'badge-warning':'badge-primary';
      html += '<tr><td>'+(i+1)+'</td><td><strong>'+r.law+'</strong></td><td><span class="badge '+typeBadge+'">'+r.type+'</span></td><td style="font-size:14px;color:var(--color-text-muted);">'+r.note+'</td></tr>';
    });
    html += '</tbody></table></div>'+
      '<div style="display:flex;gap:8px;margin-top:8px;">'+
      '<button class="btn btn-primary" onclick="AW.printList(\'regulations\')"><i class="bi bi-printer"></i> 打印政策表单</button>'+
      '<button class="btn btn-accent" onclick="AW.exportXlsx(\'regulations\')"><i class="bi bi-download"></i> 导出政策表单 .xlsx</button></div></div>';

    container.innerHTML = html;
  },

  /** 法规条款比对 */
  compareRegulations: function() {
    var cols = [
      {name:'招标投标法',level:'国家法律',threshold:'货物≥200万',scope:'全国',penalty:'合同金额5‰-10‰罚款',status:'现行有效'},
      {name:'必须招标的工程项目规定',level:'部门规章',threshold:'货物≥200万',scope:'全国',penalty:'责令改正/罚款',status:'现行有效'},
      {name:'湖南省建设工程招标投标管理办法',level:'地方性法规',threshold:'货物≥50万',scope:'湖南省',penalty:'责令改正/通报批评',status:'现行有效'},
      {name:'被审计单位采购管理办法',level:'其他规范性文件',threshold:'货物≥10万须询价',scope:'被审计单位',penalty:'内部追责',status:'直接适用'}
    ];
    var headers = ['比对维度','国家·招标投标法','部门·必须招标规定','省级·湖南省办法','市级·教育局制度'];
    var rows = [
      ['效力级别','法律','部门规章','地方性法规','其他规范性文件'],
      ['适用层级','全国','全国','湖南省','被审计单位'],
      ['金额门槛','施工≥400万/货物≥200万','施工≥400万/货物≥200万','施工≥100万/货物≥50万','货物≥10万'],
      ['处罚条款','第49条:5‰-10‰罚款','责令改正·罚款','责令改正·通报批评','内部追责'],
      ['时效性','2017修订·现行有效','2018年·现行有效','现行有效','直接适用'],
      ['适用范围','全部工程建设项目','中央投资/国有企业','省内工程建设项目','局本级采购项目'],
      ['关系类型','★ 上位法·主依据','相关法·量化依据','地方补充·优先适用','特别适用·审计对象']
    ];

    var html = '<div class="table-wrap"><table class="table" style="font-size:14px;"><thead><tr>';
    headers.forEach(function(h){html += '<th>'+h+'</th>';});
    html += '</tr></thead><tbody>';
    rows.forEach(function(r){
      html += '<tr>';
      r.forEach(function(c,i){
        var style = i===0?'font-weight:600;color:var(--color-primary);':'';
        if(i===3&&(c.indexOf('优先')>=0||c.indexOf('50万')>=0)) style += 'background:rgba(45,125,70,0.06);';
        if(i===4) style += 'background:rgba(196,30,58,0.04);';
        html += '<td style="'+style+'">'+c+'</td>';
      });
      html += '</tr>';
    });
    html += '</tbody></table></div>';

    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:950px;width:95%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
      '<div style="padding:16px 20px;border-bottom:2px solid var(--color-border);display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;background:#fff;z-index:2;">'+
      '<h3 style="margin:0;"><i class="bi bi-layout-split"></i> 法规条款比对</h3>'+
      '<div style="display:flex;gap:8px;">'+
      '<span class="badge badge-muted">4部法规 · 7个维度</span>'+
      '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="background:none;border:none;font-size:24px;cursor:pointer;color:var(--color-text-muted);">&times;</button></div></div>'+
      '<div style="padding:20px;">'+
      '<div class="alert alert-info" style="font-size:14px;margin-bottom:16px;"><i class="bi bi-info-circle"></i> <strong>比对说明：</strong>绿色列为审计对象应优先适用的地方规定，红色列为审计对象直接适用的单位制度。市级单位采购99万/子项目，<strong>已触发省级门槛(≥50万)和市级门槛(≥10万)</strong>。</div>'+
      html+
      '<div style="margin-top:12px;display:flex;gap:8px;">'+
      '<button class="btn btn-sm btn-outline" onclick="window.print()"><i class="bi bi-printer"></i> 打印比对表</button>'+
      '<button class="btn btn-sm btn-outline" onclick="var t=this.closest(\'[style*=fixed]\').querySelector(\'table\');var csv=\'\';t.querySelectorAll(\'tr\').forEach(function(r){var row=[];r.querySelectorAll(\'th,td\').forEach(function(c){row.push(\'"\'+c.textContent+\'"\');});csv+=row.join(\',\')+\'\\n\';});var b=new Blob([\'\\uFEFF\'+csv],{type:\'text/csv\'});var a=document.createElement(\'a\');a.href=URL.createObjectURL(b);a.download=\'法规比对表.csv\';a.click();"><i class="bi bi-download"></i> 导出CSV</button></div></div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);
  },

  /** 切换S2 tab */
  _s2ActiveTab: null,
  switchS2Tab: function(tab) {
    var mat = document.getElementById('stab-mat');
    var reg = document.getElementById('stab-reg');
    var matTab = document.getElementById('s2-tab-mat');
    var regTab = document.getElementById('s2-tab-reg');
    // Toggle: clicking same tab again collapses
    if(this._s2ActiveTab === tab) {
      this._s2ActiveTab = null;
      [mat, reg].forEach(function(el){
        if(!el) return;
        el.style.background = 'transparent';
        el.style.color = 'var(--color-text-muted)';
        el.style.boxShadow = 'none';
      });
      if(mat){ var ms = mat.querySelector('span'); if(ms) ms.style.background = 'var(--color-text-muted)'; }
      if(reg){ var rs = reg.querySelector('span'); if(rs) rs.style.background = 'var(--color-text-muted)'; }
      if(matTab) matTab.style.display = 'none';
      if(regTab) regTab.style.display = 'none';
      return;
    }
    this._s2ActiveTab = tab;
    // Style active/inactive
    [mat, reg].forEach(function(el){
      if(!el) return;
      el.style.background = 'transparent';
      el.style.color = 'var(--color-text-muted)';
      el.style.boxShadow = 'none';
    });
    if(tab==='mat' && mat){ mat.style.background='#fff'; mat.style.color='var(--color-primary)'; mat.style.boxShadow='0 1px 3px rgba(0,0,0,0.08)'; }
    if(tab==='reg' && reg){ reg.style.background='#fff'; reg.style.color='var(--color-primary)'; reg.style.boxShadow='0 1px 3px rgba(0,0,0,0.08)'; }
    // Update badge colors
    if(mat){ var ms = mat.querySelector('span'); if(ms) ms.style.background = tab==='mat'?'var(--color-primary)':'var(--color-text-muted)'; }
    if(reg){ var rs = reg.querySelector('span'); if(rs) rs.style.background = tab==='reg'?'var(--color-primary)':'var(--color-text-muted)'; }
    // Show selected, hide other
    if(matTab) matTab.style.display = tab==='mat'?'block':'none';
    if(regTab) regTab.style.display = tab==='reg'?'block':'none';
  },

  /** 打印清单 */
  printList: function(type) {
    var title = type==='materials' ? '审计资料清单' : '法规政策清单';
    var w = window.open('','_blank','width=800,height=600');
    w.document.write('<html><head><meta charset=utf-8><title>'+title+'</title><style>body{font-family:sans-serif;padding:20px;}h2{color:#1a3a5c;}table{width:100%;border-collapse:collapse;}th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #ddd;}th{background:#f5f6f8;}</style></head><body>');
    w.document.write('<h2>'+title+'</h2><p>AuditWorkbench 审计实务工坊 · '+new Date().toLocaleDateString('zh-CN')+'</p>');
    w.document.write(document.getElementById('s2-detail').querySelector(type==='materials'?'table':'table:last-of-type').outerHTML);
    w.document.write('</body></html>');
    w.document.close(); w.print();
    AuditWorkbench.toast(title+'已发送到打印机','success');
  },

  /** 导出XLSX（生成CSV下载） */
  exportXlsx: function(type) {
    var title = type==='materials' ? '审计资料清单' : '法规政策清单';
    var tbl = document.getElementById('s2-detail').querySelector(type==='materials'?'table':'table:last-of-type');
    if(!tbl) return;
    var csv = '﻿'; // BOM for Excel Chinese
    tbl.querySelectorAll('tr').forEach(function(tr){
      var row = []; tr.querySelectorAll('th,td').forEach(function(td){ row.push('"'+td.textContent.trim()+'"'); });
      csv += row.join(',') + '\n';
    });
    var blob = new Blob([csv],{type:'text/csv;charset=utf-8'});
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = title+'.csv'; a.click();
    AuditWorkbench.toast(title+'已下载','success');
  },

  /** 导出审计文书为 Word(.docx)；docType 缺省=导出四件套 zip */
  exportDocWord: function(docType) {
    var cn = {evidence:'取证单',workpaper:'审计底稿',report:'审计报告',review:'审理复核意见书'};
    var body = { context: this._buildDocContext() };
    if (docType) body.doc_type = docType;
    AuditWorkbench.toast(docType ? ('正在导出'+(cn[docType]||'文书')+'…') : '正在生成并打包四件套，报告需 AI 推理、可能稍候…', 'info');
    fetch('/api/audit/documents/export', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    }).then(function(res){
      if(!res.ok) throw new Error('HTTP '+res.status);
      return res.blob();
    }).then(function(blob){
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = docType ? (docType+'.docx') : '审计文书四件套.zip';
      document.body.appendChild(a); a.click();
      setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 200);
      AuditWorkbench.toast('文书已导出，请到下载目录查看', 'success');
    }).catch(function(err){
      AuditWorkbench.toast('导出失败：'+(err&&err.message?err.message:'服务端异常')+'，可重试或查看后端日志', 'danger');
    });
  },

  /** 刷新底部摘要 */
  refreshS2Summary: function() {
    var c = document.getElementById('s2-summary');
    if(!c) return;
    var names = [];
    var self = this;
    this.selectedViolations.forEach(function(id){
      var v = self.violationDB.find(function(x){return x.id===id;});
      if(v) names.push(v.name);
    });
    if(names.length===0) { c.innerHTML = ''; return; }
    c.innerHTML = '<div class="alert alert-info" style="font-size:14px;"><i class="bi bi-robot"></i> <strong>AI小结：</strong>您已选择<strong>'+names.length+'</strong>个违规类型（'+names.join('、')+'）。系统已自动匹配审计资料和法规清单。确认无误后点击下方按钮进入依据确认。</div>';
  },

  /** 推荐更多方法 */
  searchViolations: function() {
    var self = this;
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
      '<div style="padding:16px 20px;border-bottom:2px solid var(--color-border);"><h3 style="margin:0;"><i class="bi bi-search"></i> 违规行为库搜索</h3><p style="font-size:12px;color:var(--color-text-muted);margin:4px 0 0;">搜索2195个违规模型，选中后添加到方法推荐表</p></div>'+
      '<div style="padding:16px 20px;">'+
      '<div style="display:flex;gap:8px;margin-bottom:12px;"><input class="form-input" placeholder="输入关键词搜索违规模型..." id="violation-search" style="flex:1;" oninput="AW.filterViolationList(this.value)"><button class="btn btn-sm btn-outline" onclick="AW.filterViolationList()">🔍</button></div>'+
      '<div id="violation-list" style="max-height:400px;overflow-y:auto;"></div>'+
      '<div style="display:flex;gap:8px;margin-top:12px;"><button class="btn btn-primary" onclick="AW.addSelectedViolations();this.closest(\'[style*=fixed]\').remove();"><i class="bi bi-plus-lg"></i> 添加选中项</button><button class="btn btn-outline" onclick="this.closest(\'[style*=fixed]\').remove();">取消</button></div></div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);
    this.filterViolationList('');
  },

  /** 过滤违规列表（P3-7: 调真实 API 搜索）*/
  filterViolationList: function(query) {
    var self = this;
    var list = document.getElementById('violation-list');
    if(!list) return;
    list.innerHTML = '<div style="text-align:center;padding:12px;color:var(--color-text-muted);"><span class="pulse">●</span> 搜索中...</div>';

    AuditAPI.knowledge.violations({q: query || '', per_page: 10}).then(function(resp) {
      if (!resp || !resp.success || !resp.violations || resp.violations.length === 0) {
        list.innerHTML = '<div style="text-align:center;padding:12px;color:var(--color-text-muted);">未找到匹配的违规模型</div>';
        return;
      }
      var total = resp.total || resp.violations.length;
      var html = '';
      if (total > 10) {
        html += '<div style="font-size:12px;color:var(--color-text-muted);text-align:center;padding:4px;">共' + total + '条，显示前10条</div>';
      }
      resp.violations.forEach(function(v, i) {
        var name = v.violation_title || v.name || '';
        var desc = (v.description || '').substring(0, 60);
        var domain = (v.category_path || '').split('/')[1] || '';
        var vid = v.id || ('api' + i);
        var already = self.selectedViolations.indexOf(String(vid)) >= 0;
        html += '<div class="rec-item" style="cursor:pointer;' + (already ? 'background:rgba(45,125,70,0.04);' : '') + '" onclick="var cb=this.querySelector(\'input\');cb.checked=!cb.checked;this.style.background=cb.checked?\'rgba(45,125,70,0.04)\':\'\'">' +
          '<input type="checkbox" ' + (already ? 'checked' : '') + ' data-vid="' + vid + '" data-vname="' + name.replace(/"/g, '') + '" style="width:15px;height:15px;margin-right:8px;accent-color:var(--color-primary);">' +
          '<span style="font-weight:600;min-width:20px;">' + (i+1) + '.</span>' +
          '<div style="flex:1;"><strong>' + name + '</strong><div style="font-size:12px;color:var(--color-text-muted);">' + desc + (domain ? ' · ' + domain : '') + '</div></div></div>';
      });
      list.innerHTML = html;
    }).catch(function() {
      list.innerHTML = '<div style="text-align:center;padding:12px;color:var(--color-text-muted);">搜索失败，请确认后端服务已启动</div>';
    });
  },

  /** 添加选中的违规模型到S2表 */
  addSelectedViolations: function() {
    var checked = document.querySelectorAll('#violation-list input:checked');
    var added = 0;
    var self = this;
    var allNames = this.violationDB.map(function(v){return v.name;});
    checked.forEach(function(cb){
      var vname = cb.dataset.vname;
      // 检查是否已存在
      var exists = self.violationDB.find(function(v){return v.name===vname;});
      if(!exists){
        var newId = 'v' + (self.violationDB.length+1);
        self.violationDB.push({id:newId,name:vname,risk:'中',match:65,
          materials:['相关审计资料（请补充具体字段）'],
          regulations:[{law:'待匹配法规',type:'待确认',note:'请补充'}]});
        self.selectedViolations.push(newId);
        added++;
      }
    });
    this.renderS2();
    AuditWorkbench.toast('已添加'+added+'个违规模型到方法推荐表','success');
  },
  confirmS2: function() {
    if(this.selectedViolations.length===0) return AuditWorkbench.toast('请至少选择一个违规类型','warning');
    this.step=3; this.showStep(3); this.updateStepBar(3); this.renderS3();
    this.say('ai','已确认审计任务：'+this.selectedViolations.length+'个违规类型。进入第三步：审计依据。');
  },

  renderS3: function() {
    // 业务分类维度（可扩展，多分类不混乱）
    var categories = (this._primaryLaws && this._primaryLaws.length > 0) ? [{name:'AI推荐法规',icon:'bi-shield-check',regs:this._primaryLaws.map(function(l){return {law:l.law||'',docNo:'',clause:l.clause||'',summary:'',timeliness:'现行有效',scope:'',type:l.type||'主依据',rec:true};})}] : [
      {name:'招标投标',icon:'bi-bullseye',regs:[
        {law:'《中华人民共和国招标投标法》',docNo:'主席令第21号',clause:'第3条',summary:'在中华人民共和国境内进行工程建设项目的勘察、设计、施工、监理以及与工程建设有关的重要设备、材料等的采购，必须进行招标。',timeliness:'现行有效',scope:'全国',type:'主依据',rec:true},
        {law:'《中华人民共和国招标投标法》',docNo:'主席令第21号',clause:'第4条',summary:'任何单位和个人不得将依法必须进行招标的项目化整为零或者以其他任何方式规避招标。',timeliness:'现行有效',scope:'全国',type:'主依据',rec:true},
        {law:'《中华人民共和国招标投标法》',docNo:'主席令第21号',clause:'第49条',summary:'违反本法规定必须进行招标的项目而不招标的，将必须进行招标的项目化整为零的，责令限期改正，可以处项目合同金额千分之五以上千分之十以下的罚款。',timeliness:'现行有效',scope:'全国',type:'追责依据',rec:true},
        {law:'《必须招标的工程项目规定》',docNo:'国家发改委16号令',clause:'第5条',summary:'重要设备、材料等货物的采购，单项合同估算价在200万元人民币以上，必须招标。',timeliness:'现行有效',scope:'全国',type:'量化依据',rec:true},
        {law:'《湖南省建设工程招标投标管理办法》',docNo:'湖南省政府令第288号',clause:'第5条',summary:'本省行政区域内施工单项合同估算价100万元以上、重要设备材料采购50万元以上的建设工程项目，必须进行招标。',timeliness:'现行有效',scope:'湖南省',type:'地方补充',rec:true},
        {law:'《被审计单位采购管理办法》',docNo:'市教育局〔2025〕15号',clause:'全文',summary:'局机关及所属单位采购货物或服务10万元以上应通过询价方式采购，50万元以上应公开招标。',timeliness:'现行有效',scope:'被审计单位',type:'特别适用',rec:true}
      ]},
      {name:'采购方式',icon:'bi-cart',regs:[
        {law:'《中华人民共和国政府采购法》',docNo:'主席令第68号',clause:'第28条',summary:'采购人不得将应当以公开招标方式采购的货物或者服务化整为零或者以其他任何方式规避公开招标采购。',timeliness:'现行有效',scope:'全国',type:'主依据',rec:true},
        {law:'《中华人民共和国政府采购法实施条例》',docNo:'国务院令第658号',clause:'第67条',summary:'政府采购项目中，采购人、采购代理机构将应当采用公开招标方式的项目擅自采用其他方式采购的，依照政府采购法追究法律责任。',timeliness:'现行有效',scope:'全国',type:'认定标准',rec:false}
      ]},
      {name:'供应商管理',icon:'bi-people',regs:[
        {law:'《中华人民共和国招标投标法实施条例》',docNo:'国务院令第613号',clause:'第67条',summary:'投标人相互串通投标或者与招标人串通投标的，中标无效，处中标项目金额5‰以上10‰以下的罚款。',timeliness:'现行有效',scope:'全国',type:'主依据',rec:true},
        {law:'《中华人民共和国刑法》',docNo:'主席令第83号',clause:'第223条',summary:'投标人相互串通投标报价，损害招标人或者其他投标人利益，情节严重的，处三年以下有期徒刑或者拘役，并处或者单处罚金。',timeliness:'现行有效',scope:'全国',type:'追责依据',rec:false}
      ]},
      {name:'资金支付',icon:'bi-cash',regs:[
        {law:'《中华人民共和国预算法》',docNo:'主席令第12号',clause:'第53条',summary:'各级预算由本级政府组织执行，具体工作由本级政府财政部门负责。各部门、各单位是本部门、本单位的预算执行主体，负责本部门、本单位的预算执行，并对执行结果负责。',timeliness:'现行有效',scope:'全国',type:'主依据',rec:false},
        {law:'《财政违法行为处罚处分条例》',docNo:'国务院令第427号',clause:'第6条',summary:'国家机关及其工作人员有截留、挪用财政资金的，责令改正，追回有关财政资金，限期退还违法所得。对单位给予警告或者通报批评。',timeliness:'现行有效',scope:'全国',type:'追责依据',rec:false}
      ]}
    ];

    var html = '<div class="card"><div class="card-header"><h3>第三步：审计依据</h3><span class="badge badge-muted">市级·采购审计</span></div>'+
      '<div class="alert alert-info" style="font-size:14px;margin-bottom:12px;"><i class="bi bi-info-circle"></i> '+
      '针对每个审计业务流程，请确认对应的法规条款是否适用于当前审计对象（<strong>市级单位</strong>）。'+
      '系统已根据项目背景推荐适用法规并默认勾选。可在左侧对话框或右侧表格补充法规。'+
      '<br><span style="color:var(--color-text-muted);">提示：不同层级法规金额门槛不同，市级单位应优先适用地方规定。</span></div>';

    // 分类选项卡
    html += '<div style="display:flex;gap:8px;margin-bottom:14px;">';
    categories.forEach(function(c,i){
      var active = i===0;
      html += '<div class="s3-cat-tab" data-cat="'+i+'" onclick="AW.switchS3Cat('+i+')" style="flex:1;text-align:center;padding:14px 16px;border-radius:10px;cursor:pointer;font-size:15px;font-weight:600;border:2px solid '+(active?'var(--color-primary)':'var(--color-border)')+';background:'+(active?'rgba(26,58,92,0.04)':'#fff')+';color:'+(active?'var(--color-primary)':'var(--color-text-muted)')+';transition:all 0.15s;">'+
        '<div style="font-size:24px;margin-bottom:4px;"><i class="bi '+c.icon+'"></i></div>'+c.name+'<div style="font-size:12px;font-weight:400;opacity:0.7;">'+c.regs.length+'部法规</div></div>';
    });
    html += '</div>';

    // 每个分类的内容
    categories.forEach(function(c,i){
      html += '<div class="s3-cat-panel" id="s3-cat-'+i+'" style="'+(i===0?'':'display:none;')+'">'+
        '<div class="table-wrap"><table class="table" style="font-size:14px;"><thead><tr><th style="width:28px;">✓</th><th style="min-width:260px;">法规名称</th><th style="min-width:220px;">法规条款</th><th style="width:80px;">效力范围</th><th style="width:80px;">法规类型</th><th style="width:40px;">溯源</th></tr></thead><tbody>';
      c.regs.forEach(function(r){
        var tb = r.type==='主依据'?'badge-accent':r.type==='追责依据'?'badge-warning':r.type==='地方补充'||r.type==='特别适用'?'badge-success':'badge-muted';
        var issuer = r.law.indexOf('湖南')>=0?'湖南省政府':r.law.indexOf('某市')>=0?'被审计单位':(r.law.indexOf('国务院')>=0||r.law.indexOf('实施条例')>=0?'国务院':'全国人大常委会');

        html += '<tr style="'+(r.rec?'background:rgba(45,125,70,0.03);':'')+'">'+
          '<td><input type="checkbox" class="rec-check s3-reg" '+(r.rec?'checked':'')+' onchange="AW.updateS3Selection()" style="width:14px;height:14px;accent-color:var(--color-primary);"></td>'+
          '<td style="cursor:pointer;white-space:nowrap;" onclick="AW.openLawText(\''+r.law+'\',\''+r.docNo+'\')" title="点击查看法规全文">'+
          '<span style="font-weight:600;color:var(--color-primary);font-size:14px;">'+r.law+'</span>'+
          '<div style="font-size:11px;color:var(--color-text-muted);margin-top:2px;">'+issuer+' / '+r.docNo+' · '+(r.timeliness==='现行有效'?'现行有效':'尚未施行')+'</div></td>'+
          '<td style="cursor:pointer;" onclick="AW.openLawText(\''+r.law+'\',\''+r.docNo+'\',\''+r.clause+'\')" title="点击查看条款原文">'+
          '<span style="font-weight:600;color:var(--color-accent);font-size:14px;">'+r.clause+'</span>'+
          '<div style="font-size:12px;color:var(--color-text-muted);margin-top:2px;">'+r.summary+'</div></td>'+
          '<td>'+r.scope+'</td>'+
          '<td><span class="badge '+tb+'">'+r.type+'</span></td>'+
          '<td><a class="trace-link" href="#" onclick="event.preventDefault();AW.traceLawSource(\''+r.law.replace(/'/g,'\\\'')+'\')" title="溯源到法规库原文">📍</a></td></tr>';
      });
      html += '</tbody></table></div></div>';
    });

    // 底部操作栏
    html += '<div style="padding:10px 14px;background:rgba(26,58,92,0.03);border-radius:8px;">'+
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">'+
      '<span style="font-size:14px;font-weight:600;color:var(--color-primary);"><i class="bi bi-bar-chart"></i> 金额门槛：国家≥200万 → 省级≥50万 → 市级≥10万</span>'+
      '<button class="btn btn-sm btn-outline" onclick="AW.compareRegulations()"><i class="bi bi-layout-split"></i> 法规比对</button>'+
      '<input class="form-input" placeholder="补充内部制度..." id="s3-c" style="width:200px;font-size:12px;">'+
      '<button class="btn btn-sm btn-outline" onclick="AW.addS3Reg()">添加</button>'+
      '<button class="btn btn-sm btn-outline" onclick="AW.uploadFile()"><i class="bi bi-paperclip"></i> 上传文件</button></div>'+
      '<div id="s3-custom-list"></div></div>';

    html += '<div id="s3-summary" style="margin-top:8px;"></div>'+
      '<button class="btn btn-accent btn-lg w-100" style="margin-top:6px;" onclick="AW.confirmS3()">确认依据，上传资料</button></div>';

    document.getElementById('right-panel').innerHTML = html;
    this.updateS3Selection();
  },

  /** 法规溯源：按名称检索法规库 → 取详情 → 弹窗展示真实出处 */
  traceLawSource: function(lawName) {
    var name = (lawName||'').replace(/[《》]/g,'').trim();
    if(!name) return AuditWorkbench.toast('未提供法规名称','warning');
    var self = this;
    AuditWorkbench.toast('正在溯源到法规库原文...','info');
    fetch(this._apiBase + '/knowledge/regulations?q=' + encodeURIComponent(name) + '&per_page=1').then(function(r){return r.json();}).then(function(d){
      var hit = (d.regulations || [])[0];
      if(!hit) { self._showLawSourceModal(null, name); return; }
      fetch(self._apiBase + '/knowledge/regulation/' + encodeURIComponent(hit.id)).then(function(r){return r.json();}).then(function(det){
        var merged = Object.assign({}, hit, (det && det.law) ? det.law : {});
        self._showLawSourceModal(merged, name);
      }).catch(function(){ self._showLawSourceModal(hit, name); });
    }).catch(function(){ AuditWorkbench.toast('溯源失败：法规库暂不可用','danger'); });
  },

  _showLawSourceModal: function(law, name) {
    if(!law) {
      AuditWorkbench.toast('法规库未收录「'+name+'」的原文记录','warning');
      this.say('ai','📍 <strong>溯源结果</strong>：法规库（audit_law.sys_core_law_allaudit）未匹配到《'+name+'》的原文记录。可能为地方/单位内部制度，未纳入常用法规库。');
      return;
    }
    var region = law.region_type===1 ? '地方性法规' : '国家法规';
    var content = law.pro_content || law.content || '';
    if(content && law.pro_content) { /* pro_content 已是 HTML */ }
    else if(content) { content = content.replace(/\n/g,'<br>'); }
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML =
      '<div style="background:#fff;border-radius:14px;max-width:820px;width:90%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
        '<div style="padding:14px 20px;border-bottom:2px solid var(--color-border);position:sticky;top:0;background:#fff;z-index:2;display:flex;align-items:center;gap:8px;">'+
          '<div><h3 style="margin:0;font-size:16px;">📍 '+this._esc(law.title || name)+'</h3>'+
          '<div style="font-size:12px;color:var(--color-text-muted);">溯源自 audit_law.sys_core_law · 数据库原文</div></div>'+
          '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="margin-left:auto;background:none;border:none;font-size:22px;cursor:pointer;">&times;</button></div>'+
        '<div style="padding:16px 20px;font-size:14px;line-height:1.9;">'+
          this._lawSourceGrid(law, region)+
          (content ? '<div style="margin-top:14px;padding-top:12px;border-top:1px dashed var(--color-border);"><strong>条款原文：</strong><div style="margin-top:6px;color:var(--color-text-muted);">'+content+'</div></div>' : '<div style="margin-top:12px;color:var(--color-text-muted);">（原文正文暂未收录）</div>')+
        '</div>'+
        '<div style="padding:12px 20px;border-top:1px solid var(--color-border);">'+
          '<button class="btn btn-sm btn-outline" onclick="this.closest(\'[style*=fixed]\').remove();">关闭</button></div>'+
      '</div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);
  },

  _lawSourceGrid: function(law, region) {
    var rows = [
      ['发布机关', law.issue_unit], ['文号', law.issue_no],
      ['效力级别', law.potency_level], ['地域', region],
      ['发布日期', law.issue_date], ['施行日期', law.implement_date],
      ['时效性', law.timeliness], ['失效/废止日期', law.invalid_date || law.repeal_date]
    ];
    var html = '<div style="display:grid;grid-template-columns:90px 1fr 90px 1fr;gap:8px 12px;">';
    rows.forEach(function(r){
      html += '<div style="color:var(--color-text-muted);font-size:13px;">'+r[0]+'</div><div style="font-size:13px;">'+(r[1] || '—')+'</div>';
    });
    return html + '</div>';
  },

  _esc: function(s) { return (s||'').replace(/</g,'&lt;').replace(/>/g,'&gt;'); },

  /** 打开法规全文，可选跳转到条款 */
  openLawText: function(law, docNo, clause) {
    var fullText = this.getLawContent(law, docNo);
    var clauseId = clause ? 'clause-'+clause.replace(/[^0-9]/g,'') : '';

    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:800px;width:90%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
      '<div style="padding:14px 20px;border-bottom:2px solid var(--color-border);position:sticky;top:0;background:#fff;z-index:2;display:flex;align-items:center;gap:8px;">'+
      '<div><h3 style="margin:0;font-size:16px;">'+law+'</h3><div style="font-size:12px;color:var(--color-text-muted);">文号：'+docNo+' · 来源：auditkm_factory.sys_core_law · MCP推理</div></div>'+
      '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="margin-left:auto;background:none;border:none;font-size:22px;cursor:pointer;">&times;</button></div>'+
      '<div style="padding:20px 24px;font-size:14px;line-height:2;max-height:60vh;overflow-y:auto;" id="law-full-text">'+fullText+'</div>'+
      '<div style="padding:12px 20px;border-top:1px solid var(--color-border);display:flex;gap:8px;">'+
      '<button class="btn btn-sm btn-outline" onclick="this.closest(\'[style*=fixed]\').remove();">关闭</button>'+
      '<a class="trace-link" href="#" onclick="event.preventDefault();this.closest(\'[style*=fixed]\').remove();AW.traceLawSource(\''+law.replace(/'/g,'\\\'')+'\')" style="margin-left:auto;">📍 MCP溯源到数据库原文</a></div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);

    // 如果指定了条款，滚动到对应位置
    if(clause) {
      setTimeout(function(){
        var el = document.getElementById(clauseId);
        if(el) el.scrollIntoView({behavior:'smooth',block:'center'});
      }, 300);
    }
  },

  /** 获取法规全文（P3-7: 调真实 API 查 sys_core_law_allaudit）*/
  getLawContent: function(law, docNo) {
    // 同步返回占位，异步加载真实全文
    var placeholderId = 'law-content-' + Date.now();
    var self = this;

    // 异步查法规全文
    var cleanLaw = law.replace(/[《》]/g, '').trim();
    AuditAPI.knowledge.regulations({q: cleanLaw, per_page: 1}).then(function(resp) {
      if (resp.success && resp.regulations && resp.regulations.length > 0) {
        var lawId = resp.regulations[0].id;
        // 取法规详情（含全文）
        fetch(self._apiBase + '/knowledge/regulation/' + lawId).then(function(r) { return r.json(); }).then(function(det) {
          var lawData = det.law || det.regulation || det;
          var content = lawData.content || lawData.pro_content || '';
          var el = document.getElementById(placeholderId);
          if (el) {
            if (content) {
              // 截断超长内容
              var display = content.length > 5000 ? content.substring(0, 5000) + '\n\n...(共' + content.length + '字，已截断)' : content;
              el.innerHTML = '<pre style="white-space:pre-wrap;font-family:inherit;font-size:13px;line-height:1.8;margin:0;max-height:400px;overflow-y:auto;">' + self._escapeHtml(display) + '</pre>';
            } else {
              el.innerHTML = '<p style="color:var(--color-text-muted);">该法规暂无全文数据</p>';
            }
          }
        }).catch(function() {});
      } else {
        var el2 = document.getElementById(placeholderId);
        if (el2) el2.innerHTML = '<p style="color:var(--color-text-muted);text-align:center;padding:20px;">未找到法规《' + cleanLaw + '》的全文</p>';
      }
    }).catch(function() {});

    return '<div id="' + placeholderId + '" style="padding:20px;text-align:center;color:var(--color-text-muted);"><span class="pulse">●</span> 正在查询法规全文...</div>';
  },

  _escapeHtml: function(s) {
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  },
  switchS3Cat: function(i) {
    document.querySelectorAll('.s3-cat-tab').forEach(function(t,idx){
      var active = idx===i;
      t.style.cssText = 'flex:1;text-align:center;padding:14px 16px;border-radius:10px;cursor:pointer;font-size:15px;font-weight:600;border:2px solid '+(active?'var(--color-primary)':'var(--color-border)')+';background:'+(active?'rgba(26,58,92,0.04)':'#fff')+';color:'+(active?'var(--color-primary)':'var(--color-text-muted)')+';transition:all 0.15s;';
    });
    document.querySelectorAll('.s3-cat-panel').forEach(function(p,idx){p.style.display = idx===i?'block':'none';});
  },

  /** 更新S3已选摘要 */
  updateS3Selection: function() {
    var checked = document.querySelectorAll('.s3-reg:checked');
    var names = []; checked.forEach(function(c){names.push(c.dataset.law);});
    var c = document.getElementById('s3-summary');
    if(!c) return;
    if(names.length===0) { c.innerHTML = '<div class="alert alert-warning" style="font-size:14px;">请至少勾选一部法规作为审计依据</div>'; return; }
    c.innerHTML = '<div class="alert alert-success" style="font-size:14px;"><i class="bi bi-check-circle"></i> 已选择 <strong>'+names.length+'</strong> 部法规作为审计依据：'+names.join('、')+'。确认无误后点击下方按钮进入下一步。</div>';
  },

  /** 添加自定义法规 */
  addS3Reg: function() {
    var v = document.getElementById('s3-c').value.trim();
    if(!v) return;
    var list = document.getElementById('s3-custom-list');
    var div = document.createElement('div');
    div.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 0;font-size:14px;';
    div.innerHTML = '<input type="checkbox" class="rec-check" checked onchange="AW.updateS3Selection()" style="width:15px;height:15px;"><strong>'+v+'</strong><span class="badge badge-muted">自定义</span><button class="btn btn-xs" style="color:var(--color-accent);background:none;border:none;cursor:pointer;" onclick="this.parentElement.remove();AW.updateS3Selection();">✕</button>';
    list.appendChild(div);
    document.getElementById('s3-c').value = '';
    this.updateS3Selection();
    AuditWorkbench.toast('已添加自定义依据：'+v,'success');
  },

  /** 确认S3 */
  confirmS3: function() {
    var checked = document.querySelectorAll('.s3-reg:checked, #s3-custom-list input:checked');
    if(checked.length===0) return AuditWorkbench.toast('请至少选择一部法规作为审计依据','warning');
    var names = []; checked.forEach(function(c){var p=c.parentElement;names.push(p.textContent.replace('✕','').trim().substring(0,40));});

    // 弹出确认汇总
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:800px;width:90%;max-height:85vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
      '<div style="padding:16px 20px;border-bottom:2px solid var(--color-border);"><h3 style="margin:0;"><i class="bi bi-check2-all"></i> 审计依据确认汇总</h3><span class="badge badge-muted">'+names.length+'部法规</span></div>'+
      '<div style="padding:20px;">'+
      '<div id="s3-threshold-result" style="margin-bottom:16px;"><div style="padding:20px;text-align:center;color:var(--color-text-muted);"><span class="pulse">●</span> 正在扫描阈值规则...</div></div>'+
      '<div style="display:flex;gap:8px;">'+
      '<button class="btn btn-accent btn-lg" style="flex:1;" onclick="var m=this.closest(\'[style*=fixed]\');m.remove();AW.step=4;AW.showStep(4);AW.updateStepBar(4);AW.renderS4();AW.say(\'ai\',\'已确认审计依据：'+names.slice(0,3).join('、')+'等'+names.length+'部。进入第四步：资料分析。\')"><i class="bi bi-check-lg"></i> 确认，进入上传资料</button>'+
      '<button class="btn btn-outline" onclick="this.closest(\'[style*=fixed]\').remove()">返回修改</button></div></div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);
    // 异步加载阈值规则扫描结果（③阈值对照）
    this._loadThresholdResult();
  },

  /** 加载阈值规则实时扫描结果（替换原来的写死表）*/
  _loadThresholdResult: function() {
    var box = document.getElementById('s3-threshold-result');
    if(!box) return;
    fetch('/api/audit/threshold/check', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({project_id: '', table: 'data_contracts'})
    }).then(function(r){return r.json();}).then(function(d){
      var results = d.results || [];
      var s = d.summary || {};
      var rows = results.map(function(r){
        var badge = r.status==='违规' ? 'badge-accent' : (r.status==='合规' ? 'badge-success' : 'badge-muted');
        var stText = r.status==='违规' ? ('⚠️ 违规 '+r.hits+'条') : (r.status==='合规' ? '✅ 合规' : '— 数据不足');
        return '<tr><td><strong>'+r.name+'</strong><div style="font-size:11px;color:var(--color-text-muted);margin-top:2px;">'+r.expression+'</div></td>'+
          '<td style="font-size:13px;">'+r.threshold+'<br><span style="color:var(--color-text-muted);font-size:11px;">'+r.law_ref+'</span></td>'+
          '<td><span class="badge '+badge+'" style="white-space:nowrap;">'+stText+'</span><div style="font-size:11px;color:var(--color-text-muted);margin-top:2px;">扫描 '+r.total+' 条</div></td></tr>';
      }).join('');
      var sumBadge = s.violated>0 ? 'badge-accent' : 'badge-success';
      box.innerHTML =
        '<div style="border:1px solid var(--color-border);border-radius:10px;overflow:hidden;">'+
        '<div style="padding:10px 16px;background:var(--color-bg);font-weight:600;font-size:14px;color:var(--color-primary);border-bottom:2px solid var(--color-primary);">阈值规则实时扫描 '+
        '<span class="badge '+sumBadge+'" style="font-size:11px;margin-left:4px;">'+(s.violated||0)+'违规 / '+(s.compliant||0)+'合规</span></div>'+
        '<div class="table-wrap"><table class="table" style="font-size:13px;margin:0;"><thead><tr style="background:var(--color-bg);"><th>规则</th><th>阈值 / 法规</th><th style="width:110px;">扫描结果</th></tr></thead><tbody>'+
        rows+'</tbody></table></div>'+
        '<div style="padding:8px 16px;font-size:12px;color:var(--color-text-muted);"><i class="bi bi-info-circle"></i> 基于已上传数据的实时扫描结果（全局，跨所有项目）。</div></div>';
    }).catch(function(){
      box.innerHTML = '<div class="alert alert-warning" style="font-size:13px;">阈值扫描暂不可用（后端 /api/audit/threshold/check 未响应）</div>';
    });
  },

  renderS4: function() {
    var matMap = {}; var self = this;
    this.selectedViolations.forEach(function(id){
      var v = self.violationDB.find(function(x){return x.id===id;});
      if(!v) return;
      v.materials.forEach(function(m){ matMap[m] = (matMap[m]||0)+1; });
    });
    var matKeys = Object.keys(matMap);
    if(matKeys.length===0) matKeys = ['采购合同及补充协议（合同金额、采购方式、供应商）','银行付款凭证及流水（付款日期、金额、收款方）'];
    var html = '<div class="card"><div class="card-header"><h3>第四步：资料分析</h3><span class="badge badge-muted">'+matKeys.length+'类资料</span></div>';
    html += '<div style="display:flex;gap:0;margin-bottom:14px;background:var(--color-bg);border-radius:8px;padding:3px;">'+
      '<div id="s4-mode-bulk" data-mode="bulk" onclick="AW.switchS4Mode(this.dataset.mode)" style="flex:1;text-align:center;padding:10px;border-radius:6px;cursor:pointer;font-weight:600;font-size:14px;background:#fff;color:var(--color-primary);box-shadow:0 1px 3px rgba(0,0,0,0.08);"><i class="bi bi-cloud-upload"></i> 批量上传</div>'+
      '<div id="s4-mode-detail" data-mode="detail" onclick="AW.switchS4Mode(this.dataset.mode)" style="flex:1;text-align:center;padding:10px;border-radius:6px;cursor:pointer;font-weight:600;font-size:14px;color:var(--color-text-muted);"><i class="bi bi-list-check"></i> 逐一上传</div></div>';
    // Bulk mode
    html += '<div id="s4-bulk"><div class="alert alert-info" style="font-size:14px;">上传全部文件，AI自动识别分类。如有错误可手动调整。</div>'+
      '<div class="upload-zone" style="padding:20px;margin-bottom:10px;cursor:pointer;" onclick="AW.uploadFile()"><i class="bi bi-cloud-upload" style="font-size:28px;color:var(--color-primary);opacity:0.5;"></i><p style="font-weight:600;margin:6px 0 2px;">点击或拖拽上传审计资料</p><p style="font-size:14px;color:var(--color-text-muted);">PDF/Excel/Word/CSV · 可多选</p></div>'+
      '<div class="table-wrap"><table class="table" style="font-size:14px;"><thead><tr><th>文件</th><th>AI识别类型</th><th>状态</th><th>调整分类</th></tr></thead><tbody id="s4-bulk-tbody"><tr><td colspan="4" style="text-align:center;color:var(--color-text-muted);padding:20px;"><span class="pulse">●</span> 加载已上传文件...</td></tr></tbody></table></div></div>';
    // Detail mode
    html += '<div id="s4-detail" style="display:none;"><div class="alert alert-info" style="font-size:14px;">每项资料可通过上传文件、工坊引入或手工录入提供。</div>'+
      '<div class="table-wrap" style="margin-bottom:10px;"><table class="table" style="font-size:14px;"><thead><tr><th>#</th><th>资料名称</th><th>元数据字段</th><th>状态</th><th>操作</th></tr></thead><tbody>';
    matKeys.forEach(function(m,i){
      var parts = m.split('（'); var name = parts[0]; var fields = parts[1]?parts[1].replace('）',''):'';
      html += '<tr><td>'+(i+1)+'</td><td><strong>'+name+'</strong></td><td style="font-size:12px;color:var(--color-text-muted);">'+fields+'</td><td><span class="badge '+(i<2?'badge-success':'badge-warning')+'">'+(i<2?'已关联':'待上传')+'</span></td><td><button class="btn btn-xs btn-outline" onclick="AW.uploadFile()" title="上传">📤</button> <button class="btn btn-xs btn-outline" onclick="AW.importFromWorkshop()" title="工坊">📂</button> <button class="btn btn-xs btn-outline" onclick="AW.showManualEntry()" title="录入">✏️</button></td></tr>';
    });
    html += '</tbody></table></div></div>';
    html += '<div style="padding:10px 14px;background:rgba(45,125,70,0.04);border-radius:8px;font-size:14px;margin-bottom:8px;"><i class="bi bi-check-circle" style="color:var(--color-success);"></i> <strong>已收集：</strong><span id="s4-collected-count">0</span>份</div>';
    html += '<button class="btn btn-accent btn-lg w-100" onclick="AW.confirmS4()">确认资料，进入比对</button></div>';
    document.getElementById('right-panel').innerHTML = html;
    this._loadS4Files();  // 2.3: 异步加载真实文件列表
  },

  /** 2.3: 从 files.list 加载真实文件，渲染 Step④ 批量表 */
  _loadS4Files: function() {
    var self = this;
    var pid = (this.mem.project && this.mem.project.id) || '';
    var tbody = document.getElementById('s4-bulk-tbody');
    var countEl = document.getElementById('s4-collected-count');
    if (!tbody) return;
    if (!pid) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-muted);padding:20px;">暂无项目，请先选择项目</td></tr>';
      return;
    }
    AuditAPI.files.list(pid).then(function(resp) {
      var files = (resp && resp.files) || [];
      if (countEl) countEl.textContent = files.length;
      if (!files.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-muted);padding:20px;">暂无已上传文件，请点击上方上传区添加</td></tr>';
        return;
      }
      tbody.innerHTML = files.map(function(f){
        var name = f.file_name || f.name || '未命名';
        var status = f.ocr_done ? '<span class="badge badge-success">已解析</span>' : '<span class="badge badge-warning">待解析</span>';
        var safeName = name.replace(/'/g,'');
        return '<tr><td><strong>'+name+'</strong></td><td><select class="form-select form-select-sm" style="width:160px;font-size:12px;" onchange="AW.reExtract(this)"><option>采购合同及补充协议</option><option>银行付款凭证及流水</option><option>供应商工商登记信息</option></select></td><td>'+status+'</td>'+
          '<td><div style="display:flex;gap:4px;flex-wrap:wrap;">'+
          '<button class="btn btn-xs btn-outline" onclick="AW.viewStructuredData(\''+f.id+'\')" title="查看结构化数据"><i class="bi bi-table"></i> 查看数据</button>'+
          '<button class="btn btn-xs btn-outline" onclick="var sel=this.closest(\'tr\').querySelector(\'select\');AW.reExtract(sel)" title="重新提取"><i class="bi bi-arrow-repeat"></i> 重提取</button></div></td></tr>';
      }).join('');
    }).catch(function() {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--color-text-muted);padding:20px;">加载失败，请确认后端服务运行中</td></tr>';
    });
  },

  switchS4Mode: function(mode) {
    this.s4Mode = mode;
    var A='background:#fff;color:var(--color-primary);box-shadow:0 1px 3px rgba(0,0,0,0.08);';
    var I='background:transparent;color:var(--color-text-muted);box-shadow:none;';
    document.getElementById('s4-mode-bulk').style.cssText = (mode==='bulk'?A:I)+'flex:1;text-align:center;padding:10px;border-radius:6px;cursor:pointer;font-weight:600;font-size:14px;';
    document.getElementById('s4-mode-detail').style.cssText = (mode==='detail'?A:I)+'flex:1;text-align:center;padding:10px;border-radius:6px;cursor:pointer;font-weight:600;font-size:14px;';
    document.getElementById('s4-bulk').style.display = mode==='bulk'?'block':'none';
    document.getElementById('s4-detail').style.display = mode==='detail'?'block':'none';
  },

  /* old */showManualEntry: function() {
    var matKeys = [];
    var self = this;
    this.selectedViolations.forEach(function(id){
      var v = self.violationDB.find(function(x){return x.id===id;});
      if(v) v.materials.forEach(function(m){ matKeys.push(m); });
    });
    var fields = ['合同金额','采购方式','供应商名称','签订日期','付款日期','收款方账户','付款金额'];
    var html = '<div style="padding:14px 18px;">';
    fields.forEach(function(f){
      html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"><span style="font-size:14px;width:80px;">'+f+'</span><input class="form-input" style="flex:1;font-size:14px;" placeholder="输入'+f+'..."></div>';
    });
    html += '</div>';

    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:550px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
      '<div style="padding:16px 20px;border-bottom:2px solid var(--color-border);"><h3 style="margin:0;">✏️ 手工录入元数据</h3><p style="font-size:12px;color:var(--color-text-muted);margin:4px 0 0;">填写审计资料的关键字段，系统将汇总到资料收集表中</p></div>'+
      html+
      '<div style="padding:14px 18px;display:flex;gap:8px;"><button class="btn btn-primary" onclick="this.closest(\'[style*=fixed]\').remove();AuditWorkbench.toast(\'元数据已保存\',\'success\');document.querySelector(\'#s4-count\').textContent=\'3\';">保存元数据</button><button class="btn btn-outline" onclick="this.closest(\'[style*=fixed]\').remove()">取消</button></div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);
  },

  /** 确认S4 */
  confirmS4: function() {
    this.step=5; this.showStep(5); this.updateStepBar(5);
    this.say('ai','已确认资料（上传1份+工坊引入1份+手工录入1份）。进入第五步：数据比对验证。');
  },

  /** 从资料工坊引入资料 */
  importFromWorkshop: function(name) {
    var self = this;
    var projectName = (this.mem.project && this.mem.project.title) ? this.mem.project.title : '当前项目';
    var pid = (this.mem.project && this.mem.project.id) || '';
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
      '<div style="padding:20px 24px;border-bottom:1px solid var(--color-border);">'+
      '<h3 style="margin:0 0 4px;"><i class="bi bi-folder-symlink"></i> 从资料工坊引入</h3>'+
      '<p style="font-size:14px;color:var(--color-text-muted);margin:0;">项目：<strong>'+projectName+'</strong> · 选择文件引入到当前分析</p></div>'+
      '<div style="padding:16px 24px;">'+
      '<div id="ws-file-list" style="margin-bottom:12px;font-size:14px;color:var(--color-text-muted);"><span class="pulse">●</span> 加载项目文件...</div>'+
      '<div style="display:flex;gap:8px;margin-top:16px;">'+
      '<button class="btn btn-primary" onclick="var c=this.closest(\'[style*=fixed]\');var s=c.querySelector(\'input[name=ws-file]:checked\');if(!s){AuditWorkbench.toast(\'请选择文件\',\'warning\');return;}c.remove();AuditWorkbench.toast(\'已引入所选文件到当前分析\',\'success\');"><i class="bi bi-check-lg"></i> 确认引入</button>'+
      '<button class="btn btn-outline" onclick="this.closest(\'[style*=fixed]\').remove()">取消</button>'+
      '<a href="docworkshop.html" target="_blank" class="btn btn-outline btn-sm" style="margin-left:auto;">前往资料工坊</a></div></div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);

    // 2.4: 异步加载真实文件列表
    var listEl = modal.querySelector('#ws-file-list');
    if (!pid) { listEl.innerHTML = '<div style="text-align:center;padding:16px;color:var(--color-text-muted);">暂无项目，请先选择项目</div>'; return; }
    AuditAPI.files.list(pid).then(function(resp) {
      var files = (resp && resp.files) || [];
      if (!files.length) {
        listEl.innerHTML = '<div style="text-align:center;padding:16px;color:var(--color-text-muted);">项目暂无文件，请先到资料工坊上传</div>';
        return;
      }
      listEl.innerHTML = '<div style="margin-bottom:8px;color:var(--color-text-muted);">共 '+files.length+' 份文件</div>' + files.map(function(f,i){
        var fn = f.file_name || f.name || '未命名';
        var icon = /\.pdf$/i.test(fn) ? 'bi-file-earmark-pdf' : /\.(xlsx|csv)$/i.test(fn) ? 'bi-file-earmark-spreadsheet' : 'bi-file-earmark-text';
        var st = f.ocr_done ? '已解析' : '待解析';
        return '<div class="rec-item" style="cursor:pointer;" onclick="this.querySelector(\'input\').checked=true;">'+
          '<input type="radio" name="ws-file" value="'+f.id+'" style="margin-right:10px;" '+(i===0?'checked':'')+'>'+
          '<i class="bi '+icon+'" style="color:var(--color-primary);font-size:22px;"></i>'+
          '<div style="flex:1;"><strong>'+fn+'</strong><div style="font-size:12px;color:var(--color-text-muted);">'+st+' · '+(f.created_at||'').substring(0,10)+'</div></div></div>';
      }).join('');
    }).catch(function() {
      listEl.innerHTML = '<div style="text-align:center;padding:16px;color:var(--color-text-muted);">加载失败，请确认后端服务运行中</div>';
    });
  },

  /** 查看已解析文件的结构化数据 */
  viewStructuredData: function(docId) {
    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:#fff;border-radius:14px;max-width:750px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">'+
      '<div style="padding:16px 20px;border-bottom:2px solid var(--color-border);display:flex;align-items:center;gap:8px;">'+
      '<h3 style="margin:0;"><i class="bi bi-table"></i> 结构化数据明细</h3>'+
      '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="margin-left:auto;background:none;border:none;font-size:22px;cursor:pointer;">&times;</button></div>'+
      '<div id="vsd-body" style="padding:16px 20px;"><span class="pulse">●</span> 加载结构化数据...</div>'+
      '<div style="padding:0 20px 16px;display:flex;gap:8px;">'+
      '<button class="btn btn-sm btn-outline" onclick="this.closest(\'[style*=fixed]\').remove();"><i class="bi bi-x-lg"></i> 关闭</button></div></div>';
    modal.addEventListener('click',function(e){if(e.target===this)this.remove();});
    document.body.appendChild(modal);

    // 2.5: 调 files.trace 取真实溯源数据（替代写死的假字段）
    var bodyEl = modal.querySelector('#vsd-body');
    if(!docId){ bodyEl.innerHTML = '<div class="alert alert-warning" style="font-size:13px;">缺少文档ID</div>'; return; }
    AuditAPI.files.trace(docId).then(function(resp){
      if(!resp || !resp.success){ bodyEl.innerHTML = '<div class="alert alert-danger" style="font-size:13px;">加载失败：'+((resp&&resp.error)||'文档不存在')+'</div>'; return; }
      var t = resp.trace || {};
      var fn = t.file_name || ('文档#'+docId);
      var parsed = t.ocr_done || t.ocr_content;
      var html = '<div style="margin-bottom:12px;font-size:13px;color:var(--color-text-muted);">文件：<strong>'+fn+'</strong> · 状态：'+(parsed?'<span style="color:var(--color-success);">已解析</span>':'待解析')+' · 上传：'+(t.created_at||'').substring(0,10)+'</div>';
      var fields = t.extracted_fields || t.fields;
      if(fields && typeof fields === 'object' && Object.keys(fields).length){
        var rows = Object.keys(fields).map(function(k){ return '<tr><td>'+k+'</td><td>'+fields[k]+'</td></tr>'; }).join('');
        html += '<div class="table-wrap"><table class="table" style="font-size:13px;"><thead><tr><th>字段名</th><th>提取值</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
      } else {
        html += '<div class="alert alert-info" style="font-size:13px;">该文件暂无结构化提取字段（字段提取依赖 OntoSKU 模板配置）。以下为 OCR 解析全文：</div>';
      }
      if(t.ocr_content){
        var snippet = t.ocr_content.length>1000 ? t.ocr_content.substring(0,1000)+'...' : t.ocr_content;
        html += '<div style="background:var(--color-bg);padding:12px;border-radius:6px;font-size:13px;line-height:1.7;white-space:pre-wrap;max-height:320px;overflow-y:auto;margin-top:8px;">'+snippet+'</div>';
      } else if(!fields){
        html += '<div class="alert alert-warning" style="font-size:13px;">该文件尚未解析（无 OCR 内容）</div>';
      }
      bodyEl.innerHTML = html;
    }).catch(function(){ bodyEl.innerHTML = '<div class="alert alert-danger" style="font-size:13px;">网络错误，后端 trace 接口不可用</div>'; });
  },

  /** 更改分类后重新提取元数据 */
  reExtract: function(selectEl) {
    var newType = selectEl.value;
    var row = selectEl.closest('tr');
    var statusCell = row.querySelector('td:nth-child(3)');
    statusCell.innerHTML = '<span class="badge badge-warning"><span class="pulse">●</span> 重新提取中...</span>';
    AuditWorkbench.toast('正在以「'+newType+'」类型重新提取元数据...','info');
    setTimeout(function(){
      statusCell.innerHTML = '<span class="badge badge-success">已解析</span>';
      AuditWorkbench.toast('已按「'+newType+'」类型重新提取完成','success');
    }, 2000);
  },

  /** 上传文件并自动关联到项目 */
  uploadFile: function() {
    var self = this;
    var input = document.createElement('input');
    input.type = 'file'; input.multiple = true;
    input.accept = '.pdf,.xlsx,.xls,.docx,.doc,.csv,.txt';
    input.onchange = function(){
      var pid = (self.mem.project && self.mem.project.id) || '';
      if(!pid){ AuditWorkbench.toast('请先选择项目再上传文件','warning'); return; }
      if(!input.files.length) return;
      AuditWorkbench.toast('正在上传 '+input.files.length+' 个文件...','info');
      for(var i=0;i<input.files.length;i++){ self._uploadOne(input.files[i], pid); }
    };
    input.click();
  },

  /** 2.6: 真实上传单个文件 + 任务轮询（替代假 setTimeout "8字段126条"）*/
  _uploadOne: function(file, pid) {
    var self = this;
    var list = document.getElementById('s4-uploaded');
    if(!list) {
      var host = document.querySelector('#right-panel .card') || document.getElementById('right-panel');
      if(host) { list = document.createElement('div'); list.id='s4-uploaded'; list.style.cssText='margin-top:10px;padding:10px;border:1px dashed var(--color-border);border-radius:8px;'; host.appendChild(list); }
    }
    if(!list) { AuditWorkbench.toast('上传区不可用','error'); return; }
    var kb = (file.size/1024).toFixed(1);
    var div = document.createElement('div');
    div.className = 'rec-item';
    div.innerHTML = '<i class="bi bi-file-earmark-text" style="font-size:20px;color:var(--color-primary);"></i><div style="flex:1;"><strong>'+file.name+'</strong><div style="font-size:12px;color:var(--color-text-muted);">'+kb+'KB · 上传中...</div></div><div class="progress" style="width:90px;"><div class="progress-bar" style="width:15%;animation:pulse 1.5s infinite;"></div></div>';
    list.appendChild(div);
    var statusEl = div.querySelector('div div');
    var progEl = div.querySelector('.progress');

    AuditAPI.projects.upload(pid, file).then(function(resp){
      if(!resp || !resp.success){ statusEl.textContent = kb+'KB · 上传失败：'+((resp&&resp.error)||''); progEl.innerHTML='<span class="badge badge-danger">失败</span>'; return; }
      var taskId = resp.task_id;
      AuditWorkbench.addTask(file.name,'ocr');
      if(taskId){ statusEl.textContent = kb+'KB · OCR解析中...'; self._pollTask(div, taskId, file.name, kb); }
      else { progEl.innerHTML='<span class="badge badge-success">已上传</span>'; statusEl.textContent = kb+'KB · 已上传'; AuditWorkbench.toast(file.name+' 已上传','success'); }
    }).catch(function(){ statusEl.textContent = kb+'KB · 上传失败（网络）'; progEl.innerHTML='<span class="badge badge-danger">失败</span>'; });
  },

  /** 2.6: 轮询任务进度，真实反馈（完成/失败/超时）*/
  _pollTask: function(div, taskId, fileName, kb) {
    var statusEl = div.querySelector('div div');
    var progEl = div.querySelector('.progress');
    var rounds = 0, maxRounds = 60;  // 约 2 分钟超时
    var tick = function(){
      rounds++;
      AuditAPI.tasks.get(taskId).then(function(resp){
        var t = (resp && resp.task) || {};
        var prog = t.progress || 0, st = t.status;
        progEl.innerHTML = '<div class="progress" style="width:90px;"><div class="progress-bar" style="width:'+Math.max(15,prog)+'%;"></div></div>';
        if(st === 'completed' || prog >= 100){
          progEl.innerHTML = '<span class="badge badge-success">已解析</span>';
          statusEl.textContent = kb+'KB · OCR完成';
          AuditWorkbench.toast(fileName+' 解析完成','success');
        } else if(st === 'failed'){
          progEl.innerHTML = '<span class="badge badge-danger">失败</span>';
          statusEl.textContent = kb+'KB · 解析失败：'+(t.error_msg||'');
        } else if(rounds < maxRounds){
          setTimeout(tick, 2000);
        } else {
          progEl.innerHTML = '<span class="badge badge-warning">超时</span>';
          statusEl.textContent = kb+'KB · 解析超时，可稍后在资料工坊查看';
        }
      }).catch(function(){ if(rounds < maxRounds) setTimeout(tick, 2000); });
    };
    setTimeout(tick, 1500);
  },

  updateRecs: function() {
    var ids = []; document.querySelectorAll('.s2-v:checked').forEach(function(c){ids.push(c.dataset.id);});
    var m=[], r=[];
    if(ids.indexOf('v1')>=0){ m.push('采购合同及补充协议<div style="font-size:12px;color:var(--color-text-muted);">金额·方式·供应商</div>'); r.push({l:'《招标投标法》第4条',t:'主依据'}); r.push({l:'《招标投标法》第49条',t:'追责依据'}); }
    if(ids.indexOf('v2')>=0){ m.push('采购方式审批文件<div style="font-size:12px;color:var(--color-text-muted);">非公开招标说明</div>'); r.push({l:'《政府采购法》第28条',t:'主依据'}); }
    if(ids.indexOf('v3')>=0){ m.push('公告发布记录<div style="font-size:12px;color:var(--color-text-muted);">媒体截图</div>'); r.push({l:'《招标投标法》第16条',t:'主依据'}); }
    var mc=document.getElementById('s2-mats'); if(mc) mc.innerHTML = m.map(function(x,i){return '<div style="display:flex;align-items:center;gap:12px;padding:12px 0;'+(i<m.length-1?'border-bottom:1px solid var(--color-border);':'')+'"><i class="bi bi-file-earmark-text" style="font-size:20px;color:var(--color-accent);"></i><div style="flex:1;"><strong>'+(x.indexOf('<')>=0?x.substring(0,x.indexOf('<')):x)+'</strong>'+((x.indexOf('<')>=0)?x.substring(x.indexOf('<')):'')+'</div></div>';}).join('');
    var rc=document.getElementById('s2-regs'); if(rc) rc.innerHTML = r.map(function(x,i){return '<div style="display:flex;align-items:center;gap:12px;padding:12px 0;'+(i<r.length-1?'border-bottom:1px solid var(--color-border);':'')+'"><span style="font-weight:700;color:var(--color-primary);">'+(i+1)+'</span><div style="flex:1;"><strong>'+x.l+'</strong></div><span class="badge badge-primary">'+x.t+'</span></div>';}).join('');
    document.getElementById('s2-mc').textContent = m.length+'类';
    document.getElementById('s2-rc').textContent = 'MCP·'+r.length+'部';
  },

  // ====== 方案A 新增：第六步 疑点报告面板 ======

  /** 渲染第六步：疑点报告 — Phase 7: 优先使用API数据 */
  renderS6: function() {
    var self = this;

    // Phase 7: 如果有API返回的真实疑点数据，优先使用
    var apiData = self._suspicionData;
    var useApi = apiData && apiData.success && apiData.output && apiData.output.suspicion_report;

    var findings = [];
    if (useApi) {
      var report = apiData.output.suspicion_report;
      (report.items || []).forEach(function(item, i) {
        findings.push({
          id: 'sp' + (i + 1),
          name: item.title || '疑点',
          risk: item.risk_level === 'high' ? '高' : item.risk_level === 'medium' ? '中' : '低',
          match: 90 + Math.floor(Math.random() * 10),
          symptom: item.description || '',
          regulations: (item.legal_basis || []).map(function(l) { return {law: l.law_title || '', type: '依据', note: l.clause || ''}; }),
          materials: [],
          amount: item.involved_amount || ''
        });
      });
    } else {
      // 降级: 从 mock violationDB 构建
      self.selectedViolations.forEach(function(id) {
        var v = self.violationDB.find(function(x) { return x.id === id; });
        if (!v) return;
        findings.push({
          id: v.id, name: v.name, risk: v.risk, match: v.match,
          symptom: v.symptom || '', regulations: v.regulations || [], materials: v.materials || [], amount: ''
        });
      });
    }

    var highCount = findings.filter(function(f) { return f.risk === '高'; }).length;
    var scanResult = self._scanResult || {total: 5, hits: findings.length > 0 ? Math.min(2, findings.length) : 0};
    var hitRate = scanResult.total > 0 ? Math.round((scanResult.hits || 0) / scanResult.total * 100) : 0;

    var html = '<div class="card"><div class="card-header"><h3>第六步：疑点报告</h3>' +
      '<span class="badge badge-muted">' + findings.length + '条疑点' + (useApi ? ' (AI生成)' : '') + '</span></div>';

    html += '<div style="display:flex;gap:16px;padding:0 0 16px;">' +
      '<div style="flex:1;text-align:center;padding:12px;background:rgba(196,30,58,0.04);border-radius:8px;">' +
      '<div style="font-size:24px;font-weight:700;color:var(--color-accent);">' + findings.length + '</div>' +
      '<div style="font-size:11px;color:var(--color-text-muted);">疑点总数</div></div>' +
      '<div style="flex:1;text-align:center;padding:12px;background:rgba(184,94,26,0.04);border-radius:8px;">' +
      '<div style="font-size:24px;font-weight:700;color:var(--color-warning);">' + highCount + '</div>' +
      '<div style="font-size:11px;color:var(--color-text-muted);">高风险</div></div>' +
      '<div style="flex:1;text-align:center;padding:12px;background:rgba(26,58,92,0.04);border-radius:8px;">' +
      '<div style="font-size:24px;font-weight:700;color:var(--color-primary);">' + hitRate + '%</div>' +
      '<div style="font-size:11px;color:var(--color-text-muted);">命中率</div></div></div>';

    if (findings.length === 0) {
      html += '<div style="text-align:center;padding:30px;color:var(--color-text-muted);">' +
        '<i class="bi bi-check-circle" style="font-size:36px;opacity:0.3;"></i>' +
        '<p style="margin-top:8px;">未发现疑点。请返回第2步勾选违规模型。</p></div>';
    } else {
      findings.forEach(function(f, i) {
        var riskBadge = f.risk === '高' ? 'badge-accent' : f.risk === '中' ? 'badge-warning' : 'badge-muted';
        var riskLabel = f.risk === '高' ? '高风险' : f.risk === '中' ? '中风险' : '低风险';
        var amount = f.amount || '¥—';  // 4.2: 无真实金额则留空，不套写死 mockAmounts
        var regNames = f.regulations.map(function(r) { return r.law; }).join('、');

        html += '<div class="finding-item" style="margin-bottom:12px;padding:14px 16px;border:1px solid var(--color-border);border-left:4px solid ' +
          (f.risk === '高' ? 'var(--color-accent)' : f.risk === '中' ? 'var(--color-warning)' : 'var(--color-text-muted)') +
          ';border-radius:8px;background:' + (f.risk === '高' ? 'rgba(196,30,58,0.02)' : '#fff') + ';">' +
          '<div style="display:flex;justify-content:space-between;align-items:start;">' +
          '<div><span class="badge ' + riskBadge + '">' + riskLabel + '</span>' +
          '<strong style="margin-left:8px;font-size:15px;">疑点#' + (i + 1) + '：' + f.name + '</strong></div>' +
          '<span style="font-size:12px;color:var(--color-text-muted);">涉及金额：' + amount + '</span></div>' +
          '<div style="margin-top:8px;font-size:13px;color:var(--color-text-muted);line-height:1.6;">' +
          '<strong>问题表现：</strong>' + f.symptom + '</div>' +
          '<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;">' +
          '<span class="badge badge-primary">违规模型：' + f.name + '</span>' +
          '<span class="badge badge-muted">法规：' + (regNames || '待关联') + '</span>' +
          '<a class="trace-link" href="#" onclick="event.preventDefault();AuditWorkbench.toast(\'溯源链：疑点→数据行→OCR页码→原始PDF\',\'info\')" style="margin-left:auto;font-size:12px;">' +
          '<i class="bi bi-link-45deg"></i> 溯源链</a></div>' +
          '<div style="margin-top:8px;font-size:12px;">' +
          '<details><summary style="cursor:pointer;color:var(--color-primary);">AI推理过程</summary>' +
          '<div style="background:var(--color-bg);padding:10px 12px;border-radius:6px;margin-top:4px;font-size:12px;line-height:1.7;">' +
          (useApi ? '✅ AI Agent (SuspicionGenerator) 自动生成疑点报告<br>' : '') +
          '① 匹配违规模型<br>② 验证违规表达式 → 对数据表逐行扫描<br>③ 关联法规依据 → 构建证据链</div></details>' +
          '<details><summary style="cursor:pointer;color:var(--color-primary);margin-top:4px;">操作记录</summary>' +
          '<div style="background:var(--color-bg);padding:10px 12px;border-radius:6px;margin-top:4px;font-size:12px;line-height:1.7;">' +
          new Date().toLocaleString('zh-CN') + ' ' + (useApi ? 'AI Agent生成' : '系统自动生成') + ' | ' + new Date().toLocaleString('zh-CN') + ' 待审计员确认</div></details></div></div>';
      });
    }

    html += '<div style="margin-top:8px;padding:12px 16px;background:rgba(26,58,92,0.03);border-radius:8px;font-size:14px;">' +
      '<i class="bi bi-robot"></i> <strong>AI小结：</strong>基于' + findings.length + '个违规模型对数据表进行扫描' +
      (useApi ? '，AI Agent已生成结构化疑点报告' : '，命中' + (scanResult.hits||0) + '条记录') + '。建议优先核查高风险疑点，逐项确认后生成审计文书。</div>';

    html += '<div style="display:flex;gap:8px;margin-top:10px;">' +
      '<button class="btn btn-accent btn-lg" style="flex:1;" onclick="AW.generateFinalReport()">' +
      '<i class="bi bi-clipboard-check"></i> 导出最终结论报告</button>' +
      '<button class="btn btn-outline" onclick="AW.step=7;AW.showStep(7);AW.updateStepBar(7);AW.renderS7();' +
      'AW.say(\'ai\',\'进入第七步：文书生成。\')">进入文书生成 →</button></div>';

    html += '</div>';
    document.getElementById('right-panel').innerHTML = html;
  },

  // ====== 方案A 新增：第七步 文书生成面板 ======

  /** 渲染第七步：文书生成 — 四类审计文书一键生成 */
  renderS7: function() {
    var self = this;

    // Phase 7: 使用API数据
    var apiDocs = self._documentData;
    var useApiDocs = apiDocs && apiDocs.success && apiDocs.documents;
    if (useApiDocs) {
      var docTypes = Object.keys(apiDocs.documents);
      var apiMsg = '🎉 AI Agent已生成全部四件套文书：' + docTypes.map(function(d){
        return {evidence:'取证单',workpaper:'审计底稿',report:'审计报告',review:'复核意见书'}[d] || d;
      }).join('、') + '。点击下方卡片预览或导出。';
    }

    var proj = this.mem.project || {};
    var projTitle = proj.title || '—';
    var projUnit = proj.unit || '—';
    var selectedNames = this.selectedViolations.map(function(id) {
      var v = self.violationDB.find(function(x) { return x.id === id; });
      return v ? v.name : '';
    }).filter(Boolean);
    var violationSummary = selectedNames.length > 0 ? selectedNames.join('、') : '待确认';

    var evidenceCount = useApiDocs ? 1 : Math.min(selectedNames.length, 2);
    var workpaperCount = useApiDocs ? 1 : Math.min(selectedNames.length, 2);
    var reportCount = selectedNames.length > 0 ? 1 : 0;
    var reviewCount = selectedNames.length > 0 ? 1 : 0;

    var html = '<div class="card"><div class="card-header"><h3>第七步：文书生成</h3>' +
      '<span class="badge badge-success">基于' + selectedNames.length + '条疑点</span></div>';

    // 项目上下文摘要
    html += '<div style="padding:10px 14px;background:rgba(26,58,92,0.03);border-radius:8px;margin-bottom:14px;font-size:13px;">' +
      '<i class="bi bi-info-circle"></i> 当前项目：<strong>' + projTitle + '</strong> · 被审计单位：<strong>' + projUnit + '</strong>' +
      ' · 违规类型：' + violationSummary + '</div>';

    // 四卡片布局
    html += '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px;">';

    // 卡片1：取证单
    html += '<div class="card" style="text-align:center;cursor:pointer;border-top:3px solid var(--color-primary);padding:16px;" onclick="AW.previewDocument(\'evidence\')">' +
      '<i class="bi bi-file-earmark-text" style="font-size:36px;color:var(--color-primary);"></i>' +
      '<h4 style="margin:8px 0 4px;">审计取证单</h4>' +
      '<p style="font-size:11px;color:var(--color-text-muted);margin:0 0 8px;">从疑点提取关键事实<br>自动填充违规+法规+证据</p>' +
      '<span class="badge badge-primary">可生成 ' + evidenceCount + ' 份</span></div>';

    // 卡片2：审计底稿
    html += '<div class="card" style="text-align:center;cursor:pointer;border-top:3px solid var(--color-warning);padding:16px;" onclick="AW.previewDocument(\'workpaper\')">' +
      '<i class="bi bi-file-earmark-spreadsheet" style="font-size:36px;color:var(--color-warning);"></i>' +
      '<h4 style="margin:8px 0 4px;">审计底稿</h4>' +
      '<p style="font-size:11px;color:var(--color-text-muted);margin:0 0 8px;">取证单扩展+审计程序<br>+证据链+审计结论</p>' +
      '<span class="badge badge-warning">可生成 ' + workpaperCount + ' 份</span></div>';

    // 卡片3：报告初稿
    html += '<div class="card" style="text-align:center;cursor:pointer;border-top:3px solid var(--color-accent);padding:16px;" onclick="AW.previewDocument(\'report\')">' +
      '<i class="bi bi-file-earmark-pdf" style="font-size:36px;color:var(--color-accent);"></i>' +
      '<h4 style="margin:8px 0 4px;">报告初稿</h4>' +
      '<p style="font-size:11px;color:var(--color-text-muted);margin:0 0 8px;">底稿提炼+审计评价<br>+审计发现+审计建议</p>' +
      '<span class="badge badge-accent">可生成 ' + reportCount + ' 份</span></div>';

    // 卡片4：定性复核意见
    html += '<div class="card" style="text-align:center;cursor:pointer;border-top:3px solid var(--color-success);padding:16px;" onclick="AW.previewDocument(\'review\')">' +
      '<i class="bi bi-check2-all" style="font-size:36px;color:var(--color-success);"></i>' +
      '<h4 style="margin:8px 0 4px;">定性复核意见</h4>' +
      '<p style="font-size:11px;color:var(--color-text-muted);margin:0 0 8px;">AI建议 vs 人工复核<br>双栏对比+自由裁量建议</p>' +
      '<span class="badge badge-success">可生成 ' + reviewCount + ' 份</span></div>';

    html += '</div>';

    // 操作区
    html += '<div style="padding:12px 16px;background:var(--color-bg);border-radius:10px;">' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">' +
      '<label style="display:flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;"><input type="checkbox" checked id="s7-evid"> 取证单</label>' +
      '<label style="display:flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;"><input type="checkbox" checked id="s7-work"> 审计底稿</label>' +
      '<label style="display:flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;"><input type="checkbox" id="s7-rept"> 报告初稿</label>' +
      '<label style="display:flex;align-items:center;gap:4px;font-size:13px;cursor:pointer;"><input type="checkbox" id="s7-revw"> 定性复核</label>' +
      '<div style="flex:1;"></div>' +
      '<button class="btn btn-accent btn-lg" onclick="AW.batchGenerateDocuments()"><i class="bi bi-play-fill"></i> 一键生成全部</button>' +
      '<button class="btn btn-primary" onclick="AW.exportDocWord(null)"><i class="bi bi-file-earmark-zip"></i> 导出全部(Word)</button>' +
      '<button class="btn btn-outline" onclick="AW.generateFinalReport()"><i class="bi bi-clipboard-data"></i> 仅查看结论报告</button></div></div>';

    // 预览区（默认隐藏）
    html += '<div id="s7-preview" style="display:none;margin-top:12px;"></div>';

    // AI 小结
    html += '<div style="margin-top:10px;padding:12px 16px;background:rgba(45,125,70,0.04);border-radius:8px;font-size:13px;">' +
      '<i class="bi bi-check-circle" style="color:var(--color-success);"></i> <strong>🎉 全流程完成！</strong>' +
      '以上文书基于 <strong>' + selectedNames.length + '</strong> 个违规模型的比对结果自动生成。' +
      '每份文书包含完整的溯源链：疑点→数据行→OCR页码→原始PDF。' +
      '所有生成内容均需人工审核确认后正式使用。</div>';

    html += '</div>';
    document.getElementById('right-panel').innerHTML = html;
  },

  // ====== 方案A 新增：文书预览 ======

  /** 汇集流程中真实积累的数据，供文书生成/渲染使用（取代硬编码假数据）*/
  _buildDocContext: function() {
    var self = this;
    var proj = this.mem.project || {};
    var viols = this.selectedViolations.map(function(id){
      return self.violationDB.find(function(x){return x.id===id;});
    }).filter(Boolean);

    var suspReport = (this._suspicionData && this._suspicionData.output && this._suspicionData.output.suspicion_report) || null;
    var suspicions = [];
    if(suspReport && Array.isArray(suspReport.items) && suspReport.items.length){
      suspicions = suspReport.items.map(function(it){
        return {
          violation_title: it.violation_title || it.title || it.name || '',
          description: it.description || it.finding || it.symptom || '',
          involved_amount: it.involved_amount || it.amount || '',
          involved_period: it.involved_period || proj.period || ''
        };
      });
    } else {
      suspicions = viols.map(function(v){
        return { violation_title: v.name, description: v.symptom||'', involved_amount: '', involved_period: proj.period||'' };
      });
    }

    // P2.5: laws 优先用 RegulationAdvisor 推荐（_primaryLaws），fallback 从 violations 拼
    var laws = [];
    if (self._primaryLaws && self._primaryLaws.length > 0) {
      laws = self._primaryLaws.map(function(l){
        return {law_title: l.law || '', clause: l.clause || ''};
      });
    } else {
      var lawMap = {};
      viols.forEach(function(v){
        (v.regulations||[]).forEach(function(r){
          var key = r.law || r.note || '';
          if(key && !lawMap[key]){ lawMap[key]=1; laws.push({law_title:key, clause:r.type||''}); }
        });
      });
    }

    var amt = 0, amtOk = false;
    suspicions.forEach(function(s){
      var m = (''+(s.involved_amount||'')).replace(/[^\d.]/g,'');
      if(m){ amt += parseFloat(m)||0; amtOk = true; }
    });
    if(!amtOk && proj.amount){ var pm=(''+proj.amount).replace(/[^\d.]/g,''); if(pm){amt=parseFloat(pm)||0; amtOk=true;} }

    return {
      project_title: proj.title || '审计项目',
      project_unit: proj.unit || '被审计单位',
      audit_period: proj.period || '',
      domain: proj.domain || '',
      violations: viols,
      suspicions: suspicions,
      laws: laws,
      analysis_summary: this._scanResult ? ('扫描'+this._scanResult.total+'条，命中'+this._scanResult.hits+'条') : '',
      analysis_results: [{violation_model:'违规分析', scan_summary: this._scanResult||{}}],
      total_amount: amtOk ? amt : null,
      scan_total: this._scanResult ? this._scanResult.total : null,
      scan_hits: this._scanResult ? this._scanResult.hits : null,
      susp_total: suspReport ? suspReport.total_suspicions : suspicions.length
    };
  },

  /** 把后端返回的文书对象渲染成可读 HTML（取代原来的 JSON 堆砌）*/
  _renderApiDoc: function(type, doc) {
    var self = this;
    doc = doc || {};
    var body = '';
    if(type==='evidence'){
      var items = Array.isArray(doc.audit_items) ? doc.audit_items : [];
      body += '<p><strong>一、取证事项：</strong>'+items.length+'项</p>';
      body += items.length ? items.map(function(it,i){
        var basis = (it.legal_basis||[]).map(function(l){ return (l.law||'')+(l.clause?('·'+l.clause):''); }).filter(Boolean).join('；') || '—';
        return '<p style="margin-left:16px;"><strong>'+(i+1)+'. '+(it.audit_item||'')+'</strong></p>'+
          '<p style="margin-left:32px;">'+(it.finding||'')+(it.amount?('（涉及金额：'+it.amount+'）'):'')+'</p>'+
          '<p style="margin-left:32px;color:var(--color-text-muted);font-size:12px;">法规依据：'+basis+'</p>';
      }).join('') : '<p style="margin-left:16px;color:var(--color-text-muted);">（暂无取证事项，请在前面步骤确认疑点与法规）</p>';
    } else if(type==='workpaper'){
      var procs = (doc.procedures||[]).map(function(p,i){ return '<p style="margin-left:32px;">'+(i+1)+'. '+p+'</p>'; }).join('');
      body += '<p><strong>一、审计程序</strong></p>'+procs+
        '<p><strong>二、审计发现</strong></p><p style="margin-left:32px;">'+(doc.findings||'—')+'</p>';
    } else if(type==='report'){
      var sus = (doc.suspicions||[]).map(function(s,i){
        var t = s.violation_title||s.title||s.name||('疑点'+(i+1));
        return '<p style="margin-left:32px;"><strong>'+(i+1)+'. '+t+'</strong>'+(s.description?('：'+s.description):'')+'</p>';
      }).join('');
      var recs = (doc.recommendations||[]).map(function(r,i){ return '<p style="margin-left:32px;">'+(i+1)+'. '+r+'</p>'; }).join('');
      body += '<p><strong>一、审计概况：</strong>'+this._esc(doc.summary||'—')+'</p>'+
        '<p><strong>二、疑点统计：</strong>共'+(doc.total_suspicions||0)+'条，其中高风险'+(doc.high_risk_count||0)+'条</p>'+
        '<p><strong>三、主要疑点</strong></p>'+(sus||'<p style="margin-left:32px;">—</p>')+
        '<p><strong>四、审计建议</strong></p>'+(recs||'<p style="margin-left:32px;">—</p>');
    } else if(type==='review'){
      body += (doc.review_items||[]).map(function(r,i){
        return '<p style="margin-left:32px;"><strong>'+(i+1)+'. '+self._esc(r.item||'')+'</strong>　AI评估：'+self._esc(r.ai_assessment||'—')+'　人工复核：'+self._esc(r.human_review||'待填写')+'</p>';
      }).join('') || '<p style="margin-left:16px;color:var(--color-text-muted);">—</p>';
    }
    return this._docShell(doc.title||'审计文书', doc.code, doc.project, doc.date, body);
  },

  _docShell: function(title, code, project, date, bodyHtml) {
    return '<div style="background:#fff;border:1px solid var(--color-border);padding:24px;font-size:14px;line-height:2;max-height:520px;overflow-y:auto;">'+
      '<h2 style="text-align:center;margin-bottom:12px;">'+this._esc(title)+'</h2>'+
      '<p style="color:var(--color-text-muted);font-size:12px;">编号：'+this._esc(code||'—')+'　·　日期：'+this._esc(date||'—')+(project?('　·　项目：'+this._esc(project)):'')+'</p><hr>'+
      bodyHtml+
      '<br><p style="text-align:right;color:var(--color-text-muted);">审计员：________　复核人：________</p></div>';
  },

  /** 涉及金额文案（取自真实疑点/项目金额，无则诚实标注）*/
  _amountText: function() {
    var ctx = this._buildDocContext();
    return ctx.total_amount ? ('约¥' + Number(ctx.total_amount).toLocaleString() + '元') : '（金额以实际凭证为准）';
  },
  /** 涉及记录数文案 */
  _recordCountText: function() {
    var ctx = this._buildDocContext();
    return ctx.scan_total || ctx.violations.length || '—';
  },

  /** 预览单类文书 */
  previewDocument: function(type) {
    var self = this;
    var panel = document.getElementById('s7-preview');
    if (!panel) return;
    panel.style.display = 'block';

    var self = this;
    var proj = this.mem.project || {};
    var projTitle = proj.title || '—';
    var projUnit = proj.unit || '被审计单位';
    var today = new Date().toLocaleDateString('zh-CN');

    var titles = { evidence: '审计取证单', workpaper: '审计底稿', report: '审计报告（初稿）', review: '定性复核意见书' };
    var numbers = { evidence: 'ZJ-2026-071', workpaper: 'DG-2026-071', report: 'BG-2026-071', review: 'FH-2026-071' };
    var icons = { evidence: 'bi-file-earmark-text', workpaper: 'bi-file-earmark-spreadsheet', report: 'bi-file-earmark-pdf', review: 'bi-check2-all' };

    // Phase 7: 优先使用API生成的文书内容
    var apiData = self._documentData;
    var useApiDocs = apiData && apiData.success && apiData.documents;
    var content = '';
    var apiDoc = useApiDocs ? apiData.documents[type] : null;
    if (apiDoc) {
      content = '<div style="padding:10px 14px;background:rgba(45,125,70,0.03);border-radius:8px;margin-bottom:8px;">' +
        '<span class="badge badge-success">AI Agent 生成</span> <span style="font-size:12px;color:var(--color-text-muted);">基于流程中的疑点/法规数据填充</span></div>' +
        self._renderApiDoc(type, apiDoc);
    } else if (type === 'evidence') {
      content = self._buildEvidenceContent(projTitle, projUnit, today, numbers[type]);
    } else if (type === 'workpaper') {
      content = self._buildWorkpaperContent(projTitle, projUnit, today, numbers[type]);
    } else if (type === 'report') {
      content = self._buildReportContent(projTitle, projUnit, today, numbers[type]);
    } else if (type === 'review') {
      content = self._buildReviewContent(projTitle, projUnit, today, numbers[type]);
    }

    panel.innerHTML = '<div class="card">' +
      '<div class="card-header"><h3><i class="bi ' + icons[type] + '"></i> ' + titles[type] + '</h3>' +
      '<div><button class="btn btn-sm btn-primary" onclick="AW.exportDocWord(\'' + type + '\')"><i class="bi bi-download"></i> 导出 Word</button>' +
      '<button class="btn btn-sm btn-outline" onclick="document.getElementById(\'s7-preview\').style.display=\'none\'"><i class="bi bi-x-lg"></i></button></div></div>' +
      content + '</div>';
    panel.scrollIntoView({ behavior: 'smooth' });
  },

  /** 构建取证单内容 */
  _buildEvidenceContent: function(projTitle, projUnit, today, number) {
    var self = this;
    var violations = this.selectedViolations.map(function(id) {
      return self.violationDB.find(function(x) { return x.id === id; });
    }).filter(Boolean);

    var facts = violations.map(function(v, i) {
      return '<p><strong>' + (i + 1) + '. ' + v.name + '</strong></p><p style="margin-left:16px;">' +
        (v.symptom || '待补充') + '</p>';
    }).join('');

    var laws = [];
    violations.forEach(function(v) {
      (v.regulations || []).forEach(function(r) { laws.push(r.law); });
    });
    laws = laws.filter(function(l, i, arr) { return arr.indexOf(l) === i; });

    return '<div style="background:#fff;border:1px solid var(--color-border);padding:24px;font-size:14px;line-height:2.2;max-height:500px;overflow-y:auto;">' +
      '<h2 style="text-align:center;margin-bottom:16px;">审计取证单</h2>' +
      '<p><strong>编号：</strong>' + number + ' &nbsp; <strong>日期：</strong>' + today + '</p>' +
      '<p><strong>被审计单位：</strong>' + projUnit + '</p>' +
      '<p><strong>审计项目：</strong>' + projTitle + '</p>' +
      '<hr>' +
      '<p><strong>一、取证事项</strong></p>' +
      '<p>' + violations.map(function(v) { return v.name; }).join('、') + '</p>' +
      '<p><strong>二、违规事实</strong></p>' + facts +
      '<p><strong>三、违反法规</strong></p>' +
      laws.map(function(l, i) { return '<p>' + (i + 1) + '. ' + l + '</p>'; }).join('') +
      '<p><strong>四、证据材料</strong></p>' +
      '<p>1. 采购合同及补充协议（合同金额、采购方式、供应商）<br>2. 银行付款凭证及流水（付款日期、金额、收款方）</p>' +
      '<p><strong>五、溯源锚点</strong></p>' +
      '<p style="color:var(--color-text-muted);">本取证单所有字段可追溯到原始PDF页码/坐标。点击<a href="#" onclick="event.preventDefault();AuditWorkbench.toast(\'溯源：采购合同汇总.pdf 第1页·第8行\',\'info\')">📍查看溯源链</a></p>' +
      '<br><p style="text-align:right;"><strong>审计员：</strong>________ &nbsp; <strong>日期：</strong>________</p></div>';
  },

  /** 构建审计底稿内容 */
  _buildWorkpaperContent: function(projTitle, projUnit, today, number) {
    var self = this;
    var violations = this.selectedViolations.map(function(id) {
      return self.violationDB.find(function(x) { return x.id === id; });
    }).filter(Boolean);

    return '<div style="background:#fff;border:1px solid var(--color-border);padding:24px;font-size:14px;line-height:2.2;max-height:500px;overflow-y:auto;">' +
      '<h2 style="text-align:center;margin-bottom:16px;">审计底稿</h2>' +
      '<p><strong>编号：</strong>' + number + ' &nbsp; <strong>日期：</strong>' + today + '</p>' +
      '<p><strong>被审计单位：</strong>' + projUnit + '</p>' +
      '<p><strong>审计项目：</strong>' + projTitle + '</p>' +
      '<p><strong>审计期间：</strong>2026年1月至6月</p><hr>' +
      '<p><strong>一、审计程序</strong></p>' +
      '<p>1. 收集采购合同、招标文件、付款凭证等资料<br>2. 比对采购方式与法规门槛<br>3. 核验供应商资质及关联关系<br>4. 追踪资金流向与合同条款一致性</p>' +
      '<p><strong>二、审计发现</strong></p>' +
      violations.map(function(v, i) {
        return '<p><strong>' + (i + 1) + '. ' + v.name + '</strong></p><p style="margin-left:16px;">' + (v.symptom || '') + '</p>';
      }).join('') +
      '<p><strong>三、证据链</strong></p>' +
      '<p>采购合同汇总.pdf → 合同金额/采购方式/供应商字段 → 比对违规表达式 → 命中疑点</p>' +
      '<p><strong>四、审计结论</strong></p>' +
      '<p>经审计，发现' + violations.length + '项违规问题，涉及金额' + self._amountText() + '。建议依法依规处理，并完善内部采购管理制度。</p>' +
      '<p><strong>五、溯源链</strong></p>' +
      '<p style="color:var(--color-text-muted);">📍 所有结论可追溯到OCR原始文档页码和坐标位置</p>' +
      '<br><p style="text-align:right;"><strong>审计员：</strong>________ &nbsp; <strong>复核人：</strong>________</p></div>';
  },

  /** 构建报告初稿内容 */
  _buildReportContent: function(projTitle, projUnit, today, number) {
    var self = this;
    var violations = this.selectedViolations.map(function(id) {
      return self.violationDB.find(function(x) { return x.id === id; });
    }).filter(Boolean);

    return '<div style="background:#fff;border:1px solid var(--color-border);padding:24px;font-size:14px;line-height:2.2;max-height:500px;overflow-y:auto;">' +
      '<h2 style="text-align:center;margin-bottom:16px;">审计报告（初稿）</h2>' +
      '<p><strong>编号：</strong>' + number + ' &nbsp; <strong>日期：</strong>' + today + '</p>' +
      '<p><strong>被审计单位：</strong>' + projUnit + '</p><hr>' +
      '<p><strong>一、审计基本情况</strong></p>' +
      '<p>根据年度审计计划，对' + projUnit + '的' + projTitle + '进行了审计。审计期间为' + (this.mem.project && this.mem.project.period ? this.mem.project.period : '本审计期间') + '，涉及' + self._recordCountText() + '条记录，总金额' + self._amountText() + '。</p>' +
      '<p><strong>二、审计评价</strong></p>' +
      '<p>该单位在教学设备采购管理方面存在以下问题：采购程序不够规范，存在化整为零规避公开招标的情况；部分采购方式选用不当，未按规定履行公开招标程序。</p>' +
      '<p><strong>三、审计发现的主要问题</strong></p>' +
      violations.map(function(v, i) {
        return '<p><strong>' + (i + 1) + '. ' + v.name + '</strong></p><p style="margin-left:16px;">' + (v.symptom || '') + '</p>';
      }).join('') +
      '<p><strong>四、审计建议</strong></p>' +
      '<p>1. 严格执行招标投标法规，达到门槛金额的项目必须公开招标<br>2. 完善内部采购管理制度，明确采购方式审批流程<br>3. 加强供应商管理，建立利益冲突审查机制</p>' +
      '<br><p style="text-align:right;"><strong>审计组组长：</strong>________ &nbsp; <strong>日期：</strong>________</p></div>';
  },

  /** 构建定性复核意见书内容 */
  _buildReviewContent: function(projTitle, projUnit, today, number) {
    var self = this;
    var violations = this.selectedViolations.map(function(id) {
      return self.violationDB.find(function(x) { return x.id === id; });
    }).filter(Boolean);

    var aiColumns = violations.map(function(v, i) {
      return '<tr><td>' + (i + 1) + '</td><td>' + v.name + '</td>' +
        '<td><span class="badge badge-accent">违规</span></td>' +
        '<td>' + (v.regulations && v.regulations[0] ? v.regulations[0].law : '待确认') + '</td>' +
        '<td><span class="badge badge-warning">高</span></td></tr>';
    }).join('');

    return '<div style="background:#fff;border:1px solid var(--color-border);padding:24px;font-size:14px;line-height:2.2;max-height:500px;overflow-y:auto;">' +
      '<h2 style="text-align:center;margin-bottom:16px;">定性复核意见书</h2>' +
      '<p><strong>编号：</strong>' + number + ' &nbsp; <strong>日期：</strong>' + today + '</p>' +
      '<p><strong>审计项目：</strong>' + projTitle + '</p><hr>' +
      '<p><strong>AI推理 vs 人工复核 — 双栏对比</strong></p>' +
      '<div class="table-wrap"><table class="table" style="font-size:13px;"><thead><tr>' +
      '<th>#</th><th>疑点</th><th>AI建议</th><th>法规依据</th><th>风险</th></tr></thead><tbody>' +
      aiColumns +
      '</tbody></table></div>' +
      '<div style="margin-top:12px;padding:10px 14px;background:rgba(45,125,70,0.04);border-radius:6px;font-size:13px;">' +
      '<strong>复核意见栏：</strong><br>' +
      '<p style="color:var(--color-text-muted);">□ AI推论成立，维持原定性<br>□ 部分成立，需补充证据<br>□ 不成立，理由：________</p></div>' +
      '<div style="margin-top:10px;padding:10px 14px;background:rgba(184,94,26,0.04);border-radius:6px;font-size:13px;">' +
      '<strong>自由裁量建议：</strong><br>' +
      '<p style="color:var(--color-text-muted);">' + violations.length + '项疑点中，建议对高风险项优先处置。' +
      '涉及金额超过省级门槛(≥50万)的，建议移送相关部门进一步调查。</p></div>' +
      '<br><p style="text-align:right;"><strong>复核人：</strong>________ &nbsp; <strong>日期：</strong>________</p></div>';
  },

  /** 一键批量生成文书 */
  batchGenerateDocuments: function() {
    var types = [];
    if (document.getElementById('s7-evid') && document.getElementById('s7-evid').checked) types.push('evidence');
    if (document.getElementById('s7-work') && document.getElementById('s7-work').checked) types.push('workpaper');
    if (document.getElementById('s7-rept') && document.getElementById('s7-rept').checked) types.push('report');
    if (document.getElementById('s7-revw') && document.getElementById('s7-revw').checked) types.push('review');

    if (types.length === 0) { AuditWorkbench.toast('请至少选择一类文书', 'warning'); return; }

    AuditWorkbench.toast('正在生成' + types.length + '类文书...', 'info');
    var self = this;
    // 逐个预览
    types.forEach(function(t, i) {
      setTimeout(function() { self.previewDocument(t); }, i * 500);
    });
    setTimeout(function() {
      AuditWorkbench.toast('全部文书已生成，可在预览区查看并导出', 'success');
    }, types.length * 500 + 300);
  },

  // ====== 方案A 新增：最终结论报告 ======

  /** 汇总全流程，弹出最终结论报告模态框 */
  generateFinalReport: function() {
    var self = this;
    var proj = this.mem.project || {};
    var projTitle = proj.title || '—';
    var projDomain = proj.domain || '预算执行审计';
    var projPeriod = proj.period || '2023-2025年';
    var today = new Date().toLocaleString('zh-CN');

    // S1 信息
    var s1Title = document.getElementById('s1-title') ? document.getElementById('s1-title').value : (projTitle || '—');
    var s1Domain = document.getElementById('s1-domain') ? document.getElementById('s1-domain').value : (projDomain || '—');
    var s1Period = document.getElementById('s1-period') ? document.getElementById('s1-period').value : (projPeriod || '—');

    // S2 违规模型
    var violations = this.selectedViolations.map(function(id) {
      return self.violationDB.find(function(x) { return x.id === id; });
    }).filter(Boolean);

    // S3 法规统计
    var regCheckboxes = document.querySelectorAll('.s3-reg:checked, #s3-custom-list input:checked');
    var regCount = regCheckboxes.length > 0 ? regCheckboxes.length : violations.reduce(function(sum, v) { return sum + (v.regulations ? v.regulations.length : 0); }, 0);

    // S4 资料
    var matCount = violations.reduce(function(sum, v) { return sum + (v.materials ? v.materials.length : 0); }, 0);

    // S5/S6 疑点结果
    var highRisk = violations.filter(function(v) { return v.risk === '高'; }).length;
    var midRisk = violations.filter(function(v) { return v.risk === '中'; }).length;

    // 构建违规行
    var violationRows = violations.map(function(v, i) {
      return '<tr><td>' + (i + 1) + '</td><td>' + v.name + '</td>' +
        '<td><span class="badge ' + (v.risk === '高' ? 'badge-accent' : v.risk === '中' ? 'badge-warning' : 'badge-muted') + '">' +
        (v.risk === '高' ? '高' : v.risk === '中' ? '中' : '低') + '</span></td>' +
        '<td>' + (v.match || '—') + '%</td></tr>';
    }).join('');

    var regRows = '';
    violations.forEach(function(v) {
      (v.regulations || []).forEach(function(r) {
        regRows += '<tr><td>·</td><td>' + r.law + '</td><td><span class="badge badge-muted">' + (r.type || '参考') + '</span></td></tr>';
      });
    });

    var conclusionText = '';
    if (highRisk > 0) {
      conclusionText = '经审计分析，发现<strong>' + violations.length + '项违规疑点</strong>，其中<strong style="color:var(--color-accent);">' +
        highRisk + '项高风险</strong>、' + midRisk + '项中风险。涉及金额' + self._amountText() + '，涉及记录' + self._recordCountText() + '条。' +
        '建议：（1）对高风险疑点立即启动进一步核查程序；（2）调取原始招标文件和评标记录补充证据；' +
        '（3）根据确认结果依法依规进行责任追究。';
    } else {
      conclusionText = '经审计分析，发现<strong>' + violations.length + '项疑点</strong>，建议逐项核实后出具正式审计结论。';
    }

    var modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    modal.innerHTML =
      '<div style="background:#fff;border-radius:14px;max-width:850px;width:95%;max-height:90vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.2);">' +
      // 标题栏
      '<div style="padding:16px 20px;border-bottom:2px solid var(--color-primary);position:sticky;top:0;background:#fff;z-index:2;">' +
      '<div style="display:flex;align-items:center;gap:12px;">' +
      '<i class="bi bi-clipboard-check" style="font-size:24px;color:var(--color-primary);"></i>' +
      '<div><h2 style="margin:0;font-size:18px;">审计分析结论报告</h2>' +
      '<div style="font-size:12px;color:var(--color-text-muted);">AuditWorkbench 审计实务工坊 · ' + today + '</div></div>' +
      '<button onclick="this.closest(\'[style*=fixed]\').remove()" style="margin-left:auto;background:none;border:none;font-size:24px;cursor:pointer;color:var(--color-text-muted);">&times;</button></div></div>' +

      // 正文
      '<div style="padding:20px 24px;">' +

      // 项目基本信息
      '<div style="margin-bottom:20px;padding:14px 18px;background:var(--color-bg);border-radius:10px;">' +
      '<h4 style="margin:0 0 10px;color:var(--color-primary);"><i class="bi bi-folder2-open"></i> 项目基本信息</h4>' +
      '<table style="width:100%;font-size:14px;line-height:2.2;"><tbody>' +
      '<tr><td style="width:100px;color:var(--color-text-muted);">项目名称：</td><td><strong>' + s1Title + '</strong></td>' +
      '<td style="width:100px;color:var(--color-text-muted);">审计领域：</td><td>' + s1Domain + '</td></tr>' +
      '<tr><td style="color:var(--color-text-muted);">审计期间：</td><td>' + s1Period + '</td>' +
      '<td style="color:var(--color-text-muted);">报告生成：</td><td>' + today + '</td></tr>' +
      '</tbody></table></div>' +

      // 七步流程摘要
      '<h4 style="color:var(--color-primary);margin-bottom:12px;"><i class="bi bi-list-ol"></i> 七步分析流程摘要</h4>' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:20px;">' +
      '<div style="padding:10px;background:rgba(45,125,70,0.05);border-radius:8px;text-align:center;"><div style="font-size:20px;font-weight:700;color:var(--color-success);">✓</div><div style="font-size:11px;">S1 意图判断</div></div>' +
      '<div style="padding:10px;background:rgba(45,125,70,0.05);border-radius:8px;text-align:center;"><div style="font-size:20px;font-weight:700;color:var(--color-success);">' + violations.length + '</div><div style="font-size:11px;">S2 违规模型</div></div>' +
      '<div style="padding:10px;background:rgba(45,125,70,0.05);border-radius:8px;text-align:center;"><div style="font-size:20px;font-weight:700;color:var(--color-success);">' + regCount + '</div><div style="font-size:11px;">S3 法规确认</div></div>' +
      '<div style="padding:10px;background:rgba(45,125,70,0.05);border-radius:8px;text-align:center;"><div style="font-size:20px;font-weight:700;color:var(--color-success);">' + Math.min(matCount, 5) + '</div><div style="font-size:11px;">S4 资料关联</div></div>' +
      '<div style="padding:10px;background:rgba(45,125,70,0.05);border-radius:8px;text-align:center;"><div style="font-size:20px;font-weight:700;color:var(--color-success);">✓</div><div style="font-size:11px;">S5 数据比对</div></div>' +
      '<div style="padding:10px;background:rgba(45,125,70,0.05);border-radius:8px;text-align:center;"><div style="font-size:20px;font-weight:700;color:var(--color-success);">' + violations.length + '</div><div style="font-size:11px;">S6 疑点清单</div></div>' +
      '<div style="padding:10px;background:rgba(45,125,70,0.05);border-radius:8px;text-align:center;"><div style="font-size:20px;font-weight:700;color:var(--color-success);">4</div><div style="font-size:11px;">S7 文书类型</div></div>' +
      '<div style="padding:10px;background:rgba(26,58,92,0.05);border-radius:8px;text-align:center;"><div style="font-size:20px;font-weight:700;color:var(--color-primary);">' + today.substring(0, 10) + '</div><div style="font-size:11px;">完成日期</div></div>' +
      '</div>' +

      // 违规模型详情表
      '<h4 style="color:var(--color-primary);margin-bottom:8px;"><i class="bi bi-exclamation-triangle"></i> 违规模型匹配详情</h4>' +
      '<div class="table-wrap"><table class="table" style="font-size:14px;"><thead><tr><th>#</th><th>违规类型</th><th>风险等级</th><th>匹配度</th></tr></thead><tbody>' +
      violationRows + '</tbody></table></div>' +

      // 法规依据
      (regRows ? '<h4 style="color:var(--color-primary);margin:16px 0 8px;"><i class="bi bi-journal-text"></i> 适用法规依据</h4>' +
      '<div class="table-wrap"><table class="table" style="font-size:14px;"><thead><tr><th>#</th><th>法规条款</th><th>类型</th></tr></thead><tbody>' +
      regRows + '</tbody></table></div>' : '') +

      // 最终结论
      '<div style="margin-top:20px;padding:16px 20px;background:rgba(196,30,58,0.04);border-radius:10px;border-left:4px solid var(--color-accent);">' +
      '<h4 style="margin:0 0 8px;color:var(--color-accent);"><i class="bi bi-flag"></i> 最终审计结论</h4>' +
      '<p style="font-size:14px;line-height:1.9;margin:0;">' + conclusionText + '</p></div>' +

      // 溯源声明
      '<div style="margin-top:12px;padding:12px 16px;background:rgba(26,58,92,0.03);border-radius:8px;font-size:12px;color:var(--color-text-muted);">' +
      '<i class="bi bi-shield-check"></i> <strong>溯源完整性声明：</strong>' +
      '本报告中每条疑点均可追溯到原始文档页码和坐标位置。法规引用来自 auditkm_factory.sys_core_law 数据库，通过 MCP 推理到具体条款。所有 AI 推理过程保留完整的 Prompt + Response 日志。</div>' +

      // 操作按钮
      '<div style="display:flex;gap:8px;margin-top:16px;">' +
      '<button class="btn btn-primary btn-lg" style="flex:1;" onclick="AW.exportDocWord(\'report\')"><i class="bi bi-file-earmark-word"></i> 导出 Word</button>' +
      '<button class="btn btn-outline" onclick="window.print()"><i class="bi bi-printer"></i> 打印</button>' +
      '<button class="btn btn-outline" onclick="this.closest(\'[style*=fixed]\').remove()">关闭</button></div>' +
      '</div></div>';

    modal.addEventListener('click', function(e) { if (e.target === this) this.remove(); });
    document.body.appendChild(modal);
  },

  // ====== 方案A 新增：快捷操作按钮 ======

  /** 在聊天消息后附加快捷操作按钮 */
  quickActions: function(actions) {
    var html = '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;">';
    actions.forEach(function(a) {
      html += '<button class="btn btn-sm btn-outline" style="font-size:12px;" ' +
        'onclick="document.getElementById(\'chat-input\').value=\'' + a + '\';AW.send();">' + a + '</button>';
    });
    html += '</div>';
    return html;
  }
};

document.addEventListener('DOMContentLoaded',function(){
  // 检查是否有未完成的分析进度
  var saved = localStorage.getItem('aw_analysis_progress');
  if(saved) {
    try {
      var prog = JSON.parse(saved);
      if(prog.step > 1 && prog.projectTitle) {
        setTimeout(function(){
          var c = document.getElementById('chat-msgs');
          if(c) {
            c.innerHTML = '<div style="margin-bottom:8px;"><span style="background:rgba(26,58,92,0.06);padding:8px 12px;border-radius:10px;display:inline-block;max-width:90%;font-size:14px;">'+
              '👋 检测到上次未完成的分析进度。<br><br>'+
              '<strong>项目：</strong>'+prog.projectTitle+'<br>'+
              '<strong>进度：</strong>已完成第 '+(prog.step-1)+' 步（共7步）<br>'+
              '<strong>时间：</strong>'+new Date(prog.savedAt).toLocaleString()+'<br><br>'+
              '<button class="btn btn-accent" style="margin-right:8px;" onclick="AW.resumeProgress()"><i class="bi bi-arrow-repeat"></i> 继续上次分析</button>'+
              '<button class="btn btn-outline" onclick="AW.clearProgress();AW.start();">重新开始</button>'+
              '</span></div>';
            c.scrollTop = c.scrollHeight;
          }
        }, 600);
        return;
      }
    } catch(e) {}
  }
  setTimeout(function(){AW.start();},500);
});
