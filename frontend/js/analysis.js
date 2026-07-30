/**
 * AuditWorkbench — 智能分析7步向导
 */
const AnalysisWizard = {
  currentStep: 1,
  files: [],
  /** Load project context on init */
  initFromProject: function() {
    var mem = localStorage.getItem('aw_project_memory');
    if (!mem) return;
    try {
      var pm = JSON.parse(mem);
      if (pm.title) document.getElementById('parsed-title').value = pm.title;
      if (pm.domain) document.getElementById('parsed-domain').value = pm.domain;
      if (pm.level) document.getElementById('parsed-level').value = pm.level;
      if (pm.period) document.getElementById('parsed-period').value = pm.period;
      if (pm.items) document.getElementById('parsed-items').value = pm.items;
      if (pm.summary) document.getElementById('parsed-summary').value = pm.summary;
      if (pm.concerns) document.getElementById('parsed-concerns').value = pm.concerns;
      if (pm.violations && pm.violations.length > 0) {
        // Has pre-selected violations → skip to Step 3
        this.preSelectedViolations = pm.violations;
        document.getElementById('struct-panel').style.display = 'block';
        document.getElementById('parsed-actions').style.display = 'block';
        this.goToStep(3);
        AuditWorkbench.toast('已加载项目: ' + pm.title + '，请确认依据后上传资料', 'info');
      }
    } catch(e) {}
  },

  /** Navigate to a step */
  goToStep(n) {
    this.currentStep = n;
    // Update step indicator
    document.querySelectorAll('#step-indicator .step').forEach((el, i) => {
      el.classList.remove('active', 'completed');
      if (i + 1 < n) el.classList.add('completed');
      if (i + 1 === n) el.classList.add('active');
    });
    // Show/hide content
    document.querySelectorAll('.step-content').forEach(el => el.classList.remove('active'));
    const target = document.getElementById('step-' + n);
    if (target) target.classList.add('active');
    // Populate content
    if (n === 3) this.populateStep3();
    if (n === 4) this.populateStep4();
    if (n === 5) this.populateStep5();
    if (n === 6) this.populateStep6();
  },

  /** Step 1: AI parse intent → call IntentAnalyzer Agent */
  parseIntent: function() {
    var self = this;
    var intent = document.getElementById('audit-intent').value.trim();
    if (!intent) {
      AuditWorkbench.toast('请先在左侧输入审计意图描述', 'warning');
      return;
    }

    var panel = document.getElementById('struct-panel');
    panel.style.borderColor = 'var(--color-success)';
    panel.style.transition = 'border-color 0.5s';

    AuditWorkbench.toast('AI正在解析审计意图...', 'info');

    // 调用后端 IntentAnalyzer Agent
    AuditAPI.analysis.create(intent).then(function(resp) {
      if (!resp.success) {
        AuditWorkbench.toast('意图解析失败: ' + (resp.error || '未知错误'), 'error');
        panel.style.borderColor = 'var(--color-accent)';
        return;
      }

      var out = resp.intent_result || {};
      document.getElementById('parsed-title').value = out.item || intent;
      document.getElementById('parsed-domain').value = out.domain || '';
      document.getElementById('parsed-level').value = out.target_level || '';
      document.getElementById('parsed-period').value = out.period || '';
      document.getElementById('parsed-items').value = out.item || '';
      document.getElementById('parsed-concerns').value = (out.concerns || []).join('\n');
      document.getElementById('parsed-summary').value = out.estimated_scope || '';

      // 置信度提示
      if (out.confidence === 'low') {
        AuditWorkbench.toast('AI对意图的理解不够清晰，建议补充更多细节', 'warning');
      } else {
        AuditWorkbench.toast('AI已解析意图，请核对右侧结构化字段后确认', 'success');
      }

      // 存储任务ID供后续步骤使用
      self._taskId = resp.task_id || '';
      self.projectMemory = {
        title: document.getElementById('parsed-title').value,
        domain: document.getElementById('parsed-domain').value,
        level: document.getElementById('parsed-level').value,
        period: document.getElementById('parsed-period').value,
        items: document.getElementById('parsed-items').value,
        concerns: document.getElementById('parsed-concerns').value,
        summary: document.getElementById('parsed-summary').value,
        taskId: self._taskId,
        createdAt: new Date().toISOString()
      };
      localStorage.setItem('aw_project_memory', JSON.stringify(self.projectMemory));

      panel.style.borderColor = 'var(--color-primary)';
      document.getElementById('parsed-actions').style.display = 'block';
      document.getElementById('parsed-actions').scrollIntoView({ behavior: 'smooth' });
    }).catch(function(err) {
      AuditWorkbench.toast('网络错误，请确认后端服务已启动', 'error');
      panel.style.borderColor = 'var(--color-accent)';
      console.error(err);
    });
  },

  /** Step 1 → 2: Start analysis — call LangGraph workflow */
  startAnalysis: function() {
    var self = this;
    var title = document.getElementById('parsed-title').value.trim();
    var domain = document.getElementById('parsed-domain').value;
    if (!title || !domain) {
      AuditWorkbench.toast('请先点击"AI解析意图"或手动填写右侧结构化字段', 'warning');
      return;
    }
    this.projectMemory = this.projectMemory || {
      title: title, domain: domain,
      level: document.getElementById('parsed-level').value,
      period: document.getElementById('parsed-period').value,
      items: document.getElementById('parsed-items').value,
      concerns: document.getElementById('parsed-concerns').value,
      summary: document.getElementById('parsed-summary').value,
      taskId: this._taskId || '',
      createdAt: new Date().toISOString()
    };
    localStorage.setItem('aw_project_memory', JSON.stringify(this.projectMemory));
    this.goToStep(2);

    // 如果 Step① 已有结果（matches/laws），直接渲染
    if (this._step2Data) {
      document.getElementById('rec-loading').style.display = 'none';
      document.getElementById('rec-results').style.display = 'block';
      this._renderRecommendations(this._step2Data);
      return;
    }

    // 通过 step API 推进工作流到 Step②
    AuditAPI.analysis.step(this._taskId || (this.projectMemory.taskId), 2, {
      domain: domain,
      item: this.projectMemory.items || title,
      target_level: this.projectMemory.level || ''
    }).then(function(resp) {
      document.getElementById('rec-loading').style.display = 'none';
      document.getElementById('rec-results').style.display = 'block';
      if (resp.success) {
        self._step2Data = resp;
        self._renderRecommendations(resp);
      } else {
        // 降级：直接查知识库
        self._loadRecommendationsFromKnowledge();
      }
    }).catch(function() {
      document.getElementById('rec-loading').style.display = 'none';
      document.getElementById('rec-results').style.display = 'block';
      self._loadRecommendationsFromKnowledge();
    });
  },

  /** 从知识库 API 加载推荐（不依赖工作流） */
  _loadRecommendationsFromKnowledge: function() {
    var self = this;
    var domain = this.projectMemory ? (this.projectMemory.domain || '') : '';
    var keyword = this.projectMemory ? (this.projectMemory.items || this.projectMemory.title || '') : '';

    Promise.all([
      AuditAPI.knowledge.violations({q: keyword, per_page: 10}),
      AuditAPI.knowledge.regulations({q: keyword, per_page: 10})
    ]).then(function(results) {
      var vResp = results[0], rResp = results[1];
      self._renderRecommendations({
        matches: (vResp.violations || []).map(function(v) {
          return { id: v.id, violation_title: v.violation_title || v.name, description: v.description || v.desc, severity: v.severity, expression_text: v.expression_text };
        }),
        primary_laws: (rResp.regulations || []).map(function(l) {
          return { id: l.id, law_title: l.title, potency_level: l.potency_level || l.level };
        }),
        recommended_materials: []
      });
    }).catch(function() {
      document.getElementById('rec-violations').innerHTML = '<div class="alert alert-warning">无法加载推荐数据，请确认后端服务已启动</div>';
    });
  },

  /** Render recommendation results from API data */
  _renderRecommendations: function(data) {
    var level = (this.projectMemory && this.projectMemory.level) || '市级';

    // 渲染违规模型
    var matches = data.matches || [];
    if (matches.length === 0) {
      document.getElementById('rec-violations').innerHTML = '<div class="alert alert-info">未匹配到相关违规模型，请尝试调整审计意图描述</div>';
    } else {
      document.getElementById('rec-violations').innerHTML = matches.map(function(v, i) {
        var confidence = v.relevance_score || (0.95 - i * 0.05);
        return '<li class="rec-item selected">' +
          '<input type="checkbox" class="rec-check" checked data-id="' + (v.id || i) + '">' +
          '<div style="flex:1;">' +
            '<div style="font-weight:600;">' + (v.violation_title || v.name || '') + '</div>' +
            '<div style="font-size:13px;color:var(--color-text-muted);">' + (v.description || v.desc || '') + '</div>' +
            '<div class="confidence-bar" style="margin-top:4px;">' +
              '匹配度: <span class="bar-track"><span class="bar-fill high" style="width:' + (confidence*100) + '%"></span></span> ' + Math.round(confidence*100) + '%' +
            '</div>' +
          '</div>' +
          '<a class="trace-link" href="knowledge.html"><i class="bi bi-link-45deg"></i> 查看模型</a>' +
        '</li>';
      }).join('');
    }

    // 渲染推荐资料（来自 DataAdvisor）
    var materials = data.recommended_materials || [];
    if (materials.length > 0) {
      document.getElementById('rec-data').innerHTML = materials.map(function(m) {
        return '<li class="rec-item">' +
          '<input type="checkbox" class="rec-check" checked>' +
          '<div style="flex:1;">' +
            '<div style="font-weight:600;">' + (m.material_type || m.name || '') + '</div>' +
            '<div style="font-size:13px;color:var(--color-text-muted);">' + (m.reason || m.field || '') + '</div>' +
          '</div>' +
        '</li>';
      }).join('');
    } else {
      document.getElementById('rec-data').innerHTML = '<li class="rec-item"><div style="flex:1;color:var(--color-text-muted);">DataAdvisor Agent 将根据选定的违规模型推荐资料清单</div></li>';
    }

    // 渲染法规推荐
    var laws = data.primary_laws || [];
    if (laws.length === 0) {
      document.getElementById('rec-regulations').innerHTML = '<div class="alert alert-info">未匹配到相关法规，请尝试使用法规检索功能</div>';
    } else {
      document.getElementById('rec-regulations').innerHTML = laws.map(function(l, i) {
        var reasons = ['主依据', '量化依据', '关联依据', '地方补充'];
        return '<li class="rec-item selected">' +
          '<input type="checkbox" class="rec-check" checked data-id="' + (l.id || i) + '">' +
          '<div style="flex:1;">' +
            '<div style="font-weight:600;">《' + (l.law_title || l.title || '') + '" <span class="badge badge-muted">' + (l.potency_level || l.level || '') + '</span></div>' +
            '<div style="font-size:13px;color:var(--color-text-muted);">' + (l.applicable_clauses ? l.applicable_clauses.join('; ') : (l.clause || '')) + ' — ' + (l.recommendation_reason || l.reason || reasons[i % reasons.length]) + '</div>' +
          '</div>' +
          '<a class="trace-link" href="regulations.html"><i class="bi bi-link-45deg"></i> 溯源</a>' +
        '</li>';
      }).join('');
    }

    // LLM 伴随提示
    var layerAdvice = data.layer_advice || '';
    if (layerAdvice) {
      var alertDiv = document.createElement('div');
      alertDiv.className = 'alert alert-info';
      alertDiv.style.marginTop = 'var(--space-lg)';
      alertDiv.innerHTML = '<strong>LLM智能伴随：</strong>' + layerAdvice;
      document.getElementById('rec-results').appendChild(alertDiv);
    }

    // 确认按钮
    var actions = document.createElement('div');
    actions.className = 'confirm-actions';
    actions.innerHTML =
      '<button class="btn btn-primary btn-lg" onclick="AnalysisWizard.confirmAll()"><i class="bi bi-check2-all"></i> 一键确认全部</button>' +
      '<button class="btn btn-outline" onclick="AnalysisWizard.goToStep(3)">逐条查看确认 <i class="bi bi-arrow-right"></i></button>';
    document.getElementById('rec-results').appendChild(actions);
  },

  /** One-click confirm all */
  confirmAll() {
    AuditWorkbench.toast('已一键确认全部AI推荐', 'success');
    this.goToStep(4); // Skip to upload since all confirmed
  },

  /** Populate Step 3 basis list — load from workflow state */
  populateStep3: function() {
    var self = this;
    // 从 _step2Data 加载法规列表
    var laws = (this._step2Data && this._step2Data.primary_laws) || [];
    if (laws.length === 0) {
      // 降级：从知识库加载
      var keyword = this.projectMemory ? (this.projectMemory.items || this.projectMemory.title || '') : '';
      AuditAPI.knowledge.regulations({q: keyword, per_page: 10}).then(function(resp) {
        var regulations = (resp.regulations || []).slice(0, 4);
        self._renderStep3Laws(regulations);
      }).catch(function() {
        document.getElementById('basis-list').innerHTML = '<div class="alert alert-warning">无法加载法规列表</div>';
      });
    } else {
      this._renderStep3Laws(laws);
    }
  },

  _renderStep3Laws: function(laws) {
    var types = ['主依据', '量化依据', '关联依据', '地方补充'];
    if (laws.length === 0) {
      document.getElementById('basis-count').textContent = '已选 0 条';
      document.getElementById('basis-list').innerHTML = '<div class="alert alert-info">暂无法规推荐，可手动补充</div>';
    } else {
      document.getElementById('basis-count').textContent = '已选 ' + laws.length + ' 条';
      document.getElementById('basis-list').innerHTML = laws.map(function(r, i) {
        var title = r.law_title || r.title || '';
        var level = r.potency_level || r.level || '';
        return '<div class="rec-item">' +
          '<span style="font-weight:700;color:var(--color-primary);min-width:20px;">' + (i+1) + '.</span>' +
          '<div style="flex:1;">' +
            '<div style="font-weight:600;">《' + title + '》 <span class="badge badge-muted">' + level + '</span> <span class="badge badge-' + (i===0 ? 'accent' : 'primary') + '">' + (types[i % types.length]) + '</span></div>' +
            '<div style="font-size:13px;color:var(--color-text-muted);">' + (r.applicable_clauses ? r.applicable_clauses.join('; ') : (r.clause || r.article || '')) + '</div>' +
          '</div>' +
          '<a class="trace-link" href="#"><i class="bi bi-link-45deg"></i> 溯源</a>' +
          '<button class="btn btn-sm btn-outline" style="color:var(--color-accent);" onclick="this.closest(\'.rec-item\').remove()"><i class="bi bi-x-lg"></i></button>' +
        '</div>';
      }).join('');
    }
    // 自定义输入
    document.getElementById('basis-list').innerHTML +=
      '<div style="margin-top:12px;display:flex;gap:8px;">' +
        '<input type="text" class="form-input" placeholder="补充自定义法规" id="custom-law">' +
        '<button class="btn btn-outline btn-sm" onclick="AnalysisWizard.addCustomLaw()">+ 添加</button>' +
      '</div>';
  },

  addCustomLaw() {
    const input = document.getElementById('custom-law');
    const text = input.value.trim();
    if (!text) return;
    const div = document.createElement('div');
    div.className = 'rec-item';
    div.innerHTML = `<span style="font-weight:700;color:var(--color-primary);">·</span>
      <div style="flex:1;"><div style="font-weight:600;">${text}</div><div style="font-size:13px;color:var(--color-text-muted);">自定义补充</div></div>
      <a class="trace-link" href="#">📎 用户上传</a>
      <button class="btn btn-sm btn-outline" style="color:var(--color-accent);" onclick="this.closest('.rec-item').remove()"><i class="bi bi-x-lg"></i></button>`;
    document.getElementById('basis-list').insertBefore(div, input.parentElement);
    input.value = '';
    AuditWorkbench.toast('已添加自定义法规依据', 'success');
  },

  /** Step 4: Show materials - from 资料工坊 or new upload */
  populateStep4() {
    var list = document.getElementById('file-list');
    if (!list) return;
    list.innerHTML =
      '<div class="card" style="margin-bottom:8px;border:2px solid var(--color-primary);">' +
      '<div style="font-weight:600;margin-bottom:8px;"><i class="bi bi-folder-check"></i> 从资料工坊选择已处理资料</div>' +
      '<div style="font-size:13px;color:var(--color-text-muted);margin-bottom:12px;">以下为资料工坊中已解析的资料，可直接关联到本次分析</div>' +
      '<div class="rec-item" style="cursor:pointer;"><input type="checkbox" class="rec-check" checked onchange="AnalysisWizard.updateMaterialStatus()"><i class="bi bi-file-earmark-pdf" style="color:var(--color-accent);font-size:20px;"></i><div style="flex:1;"><strong>采购合同汇总.pdf</strong><div style="font-size:12px;color:var(--color-text-muted);">3.4MB · 8字段 · 5条 · 已解析</div></div><span class="badge badge-success">已解析</span><a class="trace-link" href="docworkshop.html">📍溯源</a></div>' +
      '<div class="rec-item" style="cursor:pointer;"><input type="checkbox" class="rec-check" checked onchange="AnalysisWizard.updateMaterialStatus()"><i class="bi bi-file-earmark-spreadsheet" style="color:var(--color-success);font-size:20px;"></i><div style="flex:1;"><strong>银行流水2026Q1.csv</strong><div style="font-size:12px;color:var(--color-text-muted);">856KB · 已解析</div></div><span class="badge badge-success">已解析</span><a class="trace-link" href="docworkshop.html">📍溯源</a></div>' +
      '<div class="rec-item" style="cursor:pointer;"><input type="checkbox" class="rec-check" onchange="AnalysisWizard.updateMaterialStatus()"><i class="bi bi-file-earmark-pdf" style="color:var(--color-accent);font-size:20px;"></i><div style="flex:1;"><strong>补贴发放明细.pdf</strong><div style="font-size:12px;color:var(--color-text-muted);">1.2MB · 15字段 · 1248条</div></div><span class="badge badge-success">已解析</span><a class="trace-link" href="docworkshop.html">📍溯源</a></div>' +
      '<a href="docworkshop.html" style="font-size:13px;"><i class="bi bi-box-arrow-up-right"></i> 前往资料工坊查看更多</a></div>' +
      '<div style="font-size:13px;color:var(--color-text-muted);text-align:center;margin:8px 0;">— 或上传新资料 —</div>';
  },

  updateMaterialStatus: function() {
    var checked = document.querySelectorAll('#file-list .rec-item input:checked').length;
    AuditWorkbench.toast('已选择 ' + checked + ' 份资料', 'info');
  },

  /** Step 4: File handling — 真实上传到后端 OCR */
  handleFiles: function(fileList) {
    var self = this;
    for (var i = 0; i < fileList.length; i++) {
      var f = fileList[i];
      var taskId = AuditWorkbench.addTask(f.name, 'ocr');
      self.files.push({ name: f.name, size: f.size, status: 'uploading', taskId: taskId, traceId: null });
    }
    this.renderFileList();

    // 异步上传每个文件
    this.files.forEach(function(fileObj, idx) {
      // 找到对应的 File 对象
      var file = fileList[idx];
      if (!file) return;

      AuditAPI.files.upload(self._projectId || 'default', file).then(function(resp) {
        if (resp.success) {
          fileObj.status = 'parsed';
          fileObj.traceId = resp.trace_id || null;
          fileObj.fileId = resp.file_id || null;
          AuditWorkbench.completeTask(fileObj.taskId);
          self.renderFileList();
          AuditWorkbench.toast('✅ ' + fileObj.name + ' 上传成功' + (resp.ocr_status === 'completed' ? '（OCR已解析）' : '（OCR解析中...）'), 'success');
          if (self.files.every(function(x) { return x.status === 'parsed'; })) {
            self.showMetadata();
          }
        } else {
          fileObj.status = 'error';
          self.renderFileList();
          AuditWorkbench.toast('❌ ' + fileObj.name + ' 上传失败: ' + (resp.error || '未知错误'), 'error');
        }
      }).catch(function() {
        fileObj.status = 'error';
        self.renderFileList();
        AuditWorkbench.toast('❌ ' + fileObj.name + ' 网络错误', 'error');
      });
    });

    // 提示异步处理
    document.getElementById('file-list').insertAdjacentHTML('beforeend',
      '<div class="alert alert-info" style="margin-top:8px;">' +
        '<i class="bi bi-info-circle"></i> <strong>异步处理已启动：</strong>文件上传和OCR解析在后台进行。' +
      '</div>'
    );
  },

  renderFileList() {
    document.getElementById('file-list').innerHTML = this.files.map(f => `
      <div class="rec-item">
        <i class="bi ${f.status === 'parsed' ? 'bi-file-earmark-check text-success' : 'bi-hourglass-split pulse'}" style="font-size:20px;"></i>
        <div style="flex:1;">
          <div style="font-weight:500;">${f.name}</div>
          <div style="font-size:12px;color:var(--color-text-muted);">
            ${(f.size/1024).toFixed(1)}KB
            ${f.status === 'parsed' ? ` · 提取 ${f.fields} 字段, ${f.records} 条记录` : ' · 解析中...'}
          </div>
        </div>
        ${f.status === 'parsed' ? '<a class="trace-link" href="#"><i class="bi bi-geo-alt"></i> 溯源</a>' : '<div class="progress" style="width:80px;"><div class="progress-bar" style="width:60%"></div></div>'}
      </div>
    `).join('');
  },

  showMetadata: function() {
    var self = this;
    var panel = document.getElementById('metadata-panel');
    panel.style.display = 'block';

    // 如果文件已真实上传，从 trace API 获取元数据
    var parsedFiles = this.files.filter(function(f) { return f.status === 'parsed' && f.traceId; });
    if (parsedFiles.length > 0) {
      panel.innerHTML = '<h4>提取的元数据 <span class="badge badge-warning">待确认</span></h4><div style="text-align:center;padding:10px;">正在加载溯源数据...</div>';
      AuditAPI.files.trace(parsedFiles[0].traceId).then(function(resp) {
        if (resp.success && resp.trace && resp.trace.extracted_fields) {
          var fields = resp.trace.extracted_fields;
          var rows = '';
          for (var k in fields) {
            rows += '<tr><td>' + k + '</td><td>' + (fields[k] || '—') + '</td><td><span class="badge badge-success">已提取</span></td><td><a class="trace-link" href="#">📍 定位</a></td></tr>';
          }
          panel.innerHTML = '<h4>提取的元数据 <span class="badge badge-warning">待确认</span></h4>' +
            '<div class="table-wrap" style="margin-top:8px;"><table class="table">' +
            '<thead><tr><th>字段名</th><th>提取值</th><th>状态</th><th>操作</th></tr></thead>' +
            '<tbody>' + rows + '</tbody></table></div>';
        } else {
          panel.innerHTML = '<h4>提取的元数据 <span class="badge badge-warning">待确认</span></h4>' +
            '<div class="alert alert-info">文件已上传，元数据提取中...</div>';
        }
      }).catch(function() {
        panel.innerHTML = '<h4>提取的元数据</h4><div class="alert alert-warning">无法加载元数据</div>';
      });
    } else {
      panel.innerHTML = '<h4>提取的元数据 <span class="badge badge-warning">待确认</span></h4>' +
        '<div class="alert alert-info">请先上传文件，系统将自动调用 OCR + 模板匹配提取元数据</div>';
    }
  },

  confirmMetadata() {
    AuditWorkbench.toast('元数据已确认', 'success');
    this.goToStep(5);
  },

  /** Step 5: Expression funnel — 调用真实表达式引擎 */
  populateStep5: function() {
    var self = this;
    var funnel = document.getElementById('funnel-chart');
    var detail = document.getElementById('record-detail');

    // 获取选中的违规模型（从 Step 3 确认结果）
    var selectedViolations = this._selectedViolations || [];
    if (selectedViolations.length === 0) {
      funnel.innerHTML = '<div class="alert alert-warning">请先在 Step ③ 确认违规模型和法规依据</div>';
      return;
    }

    funnel.innerHTML = '<div style="text-align:center;padding:20px;"><i class="bi bi-hourglass-split pulse"></i> 正在执行表达式扫描...</div>';

    // 对选中的第一个违规模型执行表达式
    var firstViolation = selectedViolations[0];
    var expression = firstViolation.expression_text || firstViolation.expr || '';
    if (!expression) {
      funnel.innerHTML = '<div class="alert alert-info">该违规模型无表达式定义，请使用手动分析</div>';
      return;
    }

    AuditAPI.expression.execute(expression, this._projectId || '', 'data_contracts').then(function(resp) {
      if (!resp.success) {
        funnel.innerHTML = '<div class="alert alert-warning">表达式执行失败: ' + (resp.error || '未知错误') + '</div>';
        return;
      }
      self._expressionResult = resp;
      self._renderFunnel(resp, expression);
    }).catch(function() {
      funnel.innerHTML = '<div class="alert alert-warning">表达式引擎调用失败，请确认后端服务已启动</div>';
    });

    // 详情面板骨架
    detail.innerHTML =
      '<div class="card" id="record-detail-card" style="display:none;">' +
        '<div class="card-header"><h3>逐记录比对详情</h3>' +
          '<button class="btn btn-sm btn-outline" onclick="document.getElementById(\'record-detail-card\').style.display=\'none\'"><i class="bi bi-x-lg"></i></button>' +
        '</div>' +
        '<div style="overflow-x:auto;"><table class="table" style="min-width:800px;">' +
          '<thead><tr><th>记录ID</th><th>关键字段</th><th>结果</th></tr></thead>' +
          '<tbody id="record-rows"></tbody>' +
        '</table></div>' +
      '</div>';
  },

  _renderFunnel: function(result, expression) {
    var funnel = document.getElementById('funnel-chart');
    var ast = result.ast || {};
    var hitRate = result.hit_rate || 0;

    // 构建漏斗 HTML
    var html = '<div class="funnel-container" style="margin-top:var(--space-lg);">';
    html += '<div style="text-align:center;margin-bottom:var(--space-md);"><span style="font-size:12px;color:var(--color-text-muted);">表达式扫描结果</span></div>';

    // 数据源
    html += '<div class="funnel-step">';
    html += '<div class="step-label">数据源</div>';
    html += '<div class="step-condition" style="font-size:18px;"><i class="bi bi-database"></i> 数据工坊表</div>';
    html += '<div class="step-stats"><span>全部记录: <strong>' + (result.total || 0) + ' 条</strong></span></div>';
    html += '<div class="step-bar"><div class="bar-pass" style="width:100%"></div></div>';
    html += '</div>';

    // 表达式
    html += '<div class="funnel-step">';
    html += '<div class="step-label">违规表达式</div>';
    html += '<div class="step-condition" style="font-family:monospace;font-size:14px;">' + (expression || '') + '</div>';
    html += '<div class="step-stats"><span>命中: <span class="stat-fail">' + (result.hits || 0) + ' 条</span></span><span>未命中: <span class="stat-pass">' + ((result.total || 0) - (result.hits || 0)) + ' 条</span></span></div>';
    html += '<div class="step-bar">';
    html += '<div class="bar-fail" style="width:' + (hitRate * 100) + '%"></div>';
    html += '</div>';
    html += '</div>';

    // 最终结果
    html += '<div class="funnel-result">';
    html += '<div class="result-count">' + (result.hits || 0) + ' 条命中</div>';
    html += '<div style="font-size:14px;color:var(--color-text-muted);margin-top:4px;">命中率: ' + (hitRate * 100).toFixed(1) + '%</div>';
    html += '</div>';

    // 查看详情链接
    if ((result.hits || 0) > 0) {
      html += '<div style="margin-top:8px;font-size:12px;">';
      html += '<a href="#" onclick="AnalysisWizard._showExprDetail(event)" style="color:var(--color-primary);">';
      html += '<i class="bi bi-search"></i> 查看 ' + (result.hits || 0) + ' 条命中记录详情</a></div>';
    }

    html += '</div>';
    funnel.innerHTML = html;
  },

  _showExprDetail: function(e) {
    e.preventDefault();
    var card = document.getElementById('record-detail-card');
    card.style.display = 'block';
    card.scrollIntoView({ behavior: 'smooth' });

    var rows = (this._expressionResult && this._expressionResult.rows) || [];
    document.getElementById('record-rows').innerHTML = rows.map(function(r) {
      var fields = r.fields || {};
      var keyInfo = [];
      for (var k in fields) {
        if (k === 'id' || k === 'project_id' || k === 'document_trace_id' || k === 'raw_text' || k === 'extra_fields') continue;
        keyInfo.push(k + ': ' + (fields[k] || ''));
      }
      return '<tr style="' + (r.matched ? 'background:rgba(196,30,58,0.05);' : '') + '">' +
        '<td><strong>#' + (r.row_id || '') + '</strong></td>' +
        '<td style="font-size:13px;">' + keyInfo.slice(0, 5).join(' | ') + '</td>' +
        '<td>' + (r.matched ? '<span class="badge badge-accent">🔴 命中</span>' : '<span class="badge badge-success">✅ 通过</span>') + '</td>' +
      '</tr>';
    }).join('');
  },

  runExpression: function() {
    var self = this;
    AuditWorkbench.toast('表达式已确认，正在扫描...', 'info');
    this.goToStep(6);
    // 如果表达式结果已就绪，直接填充 Step 6
    if (this._expressionResult) {
      this.populateStep6();
    }
  },

  /** Step 6: Findings — 调用 SuspicionGenerator Agent */
  populateStep6: function() {
    var self = this;
    var container = document.getElementById('findings-container');
    var countEl = document.getElementById('finding-count');

    // 调用疑点生成 API
    var analysisData = {
      analysis_results: this._expressionResult ? [this._expressionResult] : [],
      overall_assessment: '',
      domain: (this.projectMemory && this.projectMemory.domain) || '',
      item: (this.projectMemory && this.projectMemory.items) || '',
      project_id: this._projectId || ''
    };

    container.innerHTML = '<div style="text-align:center;padding:20px;"><i class="bi bi-hourglass-split pulse"></i> AI正在生成疑点报告...</div>';

    AuditAPI.suspicion.generate(analysisData).then(function(resp) {
      if (!resp.success) {
        // 降级：用表达式结果直接渲染
        self._renderFindingsFromExpression();
        return;
      }

      var report = (resp.output && resp.output.suspicion_report) || resp.suspicion_report || {};
      var items = report.items || [];

      if (items.length === 0) {
        self._renderFindingsFromExpression();
        return;
      }

      countEl.textContent = items.length + '条疑点';
      container.innerHTML = items.map(function(f, i) {
        return '<div class="finding-item risk-' + (f.risk_level || 'medium') + '">' +
          '<div style="display:flex;justify-content:space-between;align-items:start;">' +
            '<div>' +
              '<span class="badge badge-accent">' + ((f.risk_level === 'high') ? '高风险' : (f.risk_level === 'medium') ? '中风险' : '低风险') + '</span>' +
              '<strong style="margin-left:8px;">疑点#' + (i+1) + ': ' + (f.title || '') + '</strong>' +
            '</div>' +
            '<span style="font-size:13px;color:var(--color-text-muted);">涉及金额: ' + (f.involved_amount || '—') + '</span>' +
          '</div>' +
          '<div style="margin-top:8px;font-size:14px;">' + (f.description || '') + '</div>' +
          '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">' +
            '<span class="badge badge-primary">违规模型: ' + (f.violation_model || '') + '</span>' +
            (f.legal_basis && f.legal_basis.map ? f.legal_basis.map(function(l) {
              return '<span class="badge badge-muted">法规: 《' + (l.law_title || '') + '》</span>';
            }).join('') : '') +
            '<a class="trace-link" href="#" style="margin-left:auto;"><i class="bi bi-link-45deg"></i> 溯源链</a>' +
          '</div>' +
          '<div style="margin-top:8px;font-size:13px;">' +
            '<details><summary>AI推理过程</summary>' +
              '<div style="background:var(--color-bg);padding:8px;border-radius:var(--radius-sm);margin-top:4px;font-size:12px;">' +
                '匹配违规模型 → 验证违规表达式 → ' + (f.involved_amount ? f.involved_amount + '触发' : '条件触发') + ' → 确认违规' +
              '</div>' +
            '</details>' +
          '</div>' +
        '</div>';
      }).join('');

      AuditWorkbench.toast('疑点报告生成完成', 'success');
    }).catch(function() {
      self._renderFindingsFromExpression();
    });
  },

  /** 降级方案：用表达式扫描结果直接渲染 */
  _renderFindingsFromExpression: function() {
    var container = document.getElementById('findings-container');
    var countEl = document.getElementById('finding-count');
    var exprResult = this._expressionResult;

    if (!exprResult || !exprResult.hits) {
      countEl.textContent = '0条疑点';
      container.innerHTML = '<div class="alert alert-info">未发现违规疑点。表达式扫描结果: 0 条命中</div>';
      return;
    }

    countEl.textContent = exprResult.hits + '条疑点';
    var rows = exprResult.rows || [];
    container.innerHTML = rows.map(function(r, i) {
      var fields = r.fields || {};
      return '<div class="finding-item risk-medium">' +
        '<div style="display:flex;justify-content:space-between;align-items:start;">' +
          '<div>' +
            '<span class="badge badge-accent">中风险</span>' +
            '<strong style="margin-left:8px;">疑点#' + (i+1) + ': 数据记录 #' + (r.row_id || '') + '</strong>' +
          '</div>' +
        '</div>' +
        '<div style="margin-top:8px;font-size:14px;">表达式扫描命中，' + Object.keys(fields).length + ' 个字段匹配</div>' +
        '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">' +
          '<a class="trace-link" href="#"><i class="bi bi-link-45deg"></i> 溯源链</a>' +
        '</div>' +
      '</div>';
    }).join('');
    AuditWorkbench.toast('使用表达式扫描结果生成疑点', 'info');
  }
};
