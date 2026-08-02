/**
 * AuditWorkbench — API 客户端
 * 后端: OpenSquilla 网关 (同源)
 *
 * 注意: 嵌套方法内必须用 AuditAPI.base（外部对象引用），
 * 因为调用 AuditAPI.knowledge.violations() 时，方法内的 this 指向
 * knowledge 子对象（无 base 属性），会导致 fetch("undefined/api/...") 404。
 */
var AuditAPI = {
  base: window.location.origin,

  // ── 项目管理 ──
  projects: {
    list: function() {
      return fetch(AuditAPI.base + '/api/audit/projects').then(function(r) { return r.json(); });
    },
    create: function(data) {
      return fetch(AuditAPI.base + '/api/audit/projects', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    },
    get: function(id) {
      return fetch(AuditAPI.base + '/api/audit/projects/' + id).then(function(r) { return r.json(); });
    },
    delete: function(id) {
      return fetch(AuditAPI.base + '/api/audit/projects/' + id, {method: 'DELETE'}).then(function(r) { return r.json(); });
    }
  },

  // ── 文件上传 + OCR ──
  files: {
    upload: function(projectId, file, onProgress) {
      var form = new FormData();
      form.append('file', file);
      return fetch(AuditAPI.base + '/api/audit/projects/' + projectId + '/upload', {
        method: 'POST', body: form
      }).then(function(r) { return r.json(); });
    },
    list: function(projectId) {
      return fetch(AuditAPI.base + '/api/audit/projects/' + projectId + '/files').then(function(r) { return r.json(); });
    },
    trace: function(docId) {
      return fetch(AuditAPI.base + '/api/audit/documents/' + docId + '/trace').then(function(r) { return r.json(); });
    },
    reparse: function(data) {
      return fetch(AuditAPI.base + '/api/audit/documents/reparse', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 数据工坊 ──
  data: {
    tables: function(projectId) {
      return fetch(AuditAPI.base + '/api/audit/projects/' + projectId + '/data').then(function(r) { return r.json(); });
    },
    rows: function(table, params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(AuditAPI.base + '/api/audit/data/' + table + '/rows?' + qs).then(function(r) { return r.json(); });
    },
    smartQuery: function(question, projectId) {
      return fetch(AuditAPI.base + '/api/audit/data/query', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question: question, project_id: projectId})
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 知识工坊 ──
  knowledge: {
    violations: function(params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(AuditAPI.base + '/api/audit/knowledge/violations?' + qs).then(function(r) { return r.json(); });
    },
    regulations: function(params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(AuditAPI.base + '/api/audit/knowledge/regulations?' + qs).then(function(r) { return r.json(); });
    },
    regulationGraph: function(lawId) {
      return fetch(AuditAPI.base + '/api/audit/knowledge/regulation/' + lawId + '/graph').then(function(r) { return r.json(); });
    },
    clauses: function(lawId) {
      return fetch(AuditAPI.base + '/api/audit/knowledge/clauses/' + lawId).then(function(r) { return r.json(); });
    }
  },

  // ── 违规表达式 ──
  expression: {
    execute: function(expression, projectId, table) {
      return fetch(AuditAPI.base + '/api/audit/expression/execute', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({expression: expression, project_id: projectId, table: table || 'data_contracts'})
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 聚合表达式 SQL 人工确认 ──
  expressionSql: {
    listPending: function() {
      return fetch(AuditAPI.base + '/api/audit/expression-sql/pending').then(function(r) { return r.json(); });
    },
    approve: function(cid, reviewer) {
      return fetch(AuditAPI.base + '/api/audit/expression-sql/' + cid + '/approve', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({reviewer: reviewer || 'admin'})
      }).then(function(r) { return r.json(); });
    },
    reject: function(cid, reviewer) {
      return fetch(AuditAPI.base + '/api/audit/expression-sql/' + cid + '/reject', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({reviewer: reviewer || 'admin'})
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 疑点报告 ──
  suspicion: {
    generate: function(data) {
      return fetch(AuditAPI.base + '/api/audit/suspicion/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 业务阈值×法规条款对照表（P2-3）──
  thresholdTable: function(violationTitles, targetLevel) {
    return fetch(AuditAPI.base + '/api/audit/threshold-table', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({violation_titles: violationTitles || [], target_level: targetLevel || ''})
    }).then(function(r) { return r.json(); });
  },

  // ── 模板 ──
  templates: {
    list: function(params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(AuditAPI.base + '/api/audit/templates?' + qs).then(function(r) { return r.json(); });
    }
  },

  // ── 智能分析工作流 ──
  analysis: {
    create: function(intent, projectId) {
      return fetch(AuditAPI.base + '/api/audit/analysis', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({intent: intent, project_id: projectId || ''})
      }).then(function(r) { return r.json(); });
    },
    get: function(taskId) {
      return fetch(AuditAPI.base + '/api/audit/analysis/' + taskId).then(function(r) { return r.json(); });
    },
    step: function(taskId, stepNum, data) {
      return fetch(AuditAPI.base + '/api/audit/analysis/' + taskId + '/step/' + stepNum, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data || {})
      }).then(function(r) { return r.json(); });
    },
    confirm: function(taskId, data) {
      return fetch(AuditAPI.base + '/api/audit/analysis/' + taskId + '/confirm', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data || {})
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 案例库 (Phase 6) ──
  cases: {
    list: function(params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(AuditAPI.base + '/api/audit/cases?' + qs).then(function(r) { return r.json(); });
    },
    get: function(id) {
      return fetch(AuditAPI.base + '/api/audit/cases/' + id).then(function(r) { return r.json(); });
    },
    create: function(data) {
      return fetch(AuditAPI.base + '/api/audit/cases', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 语义搜索 (Phase 6) ──
  search: {
    laws: function(q, topK) {
      return fetch(AuditAPI.base + '/api/audit/search/laws?q=' + encodeURIComponent(q) + '&top_k=' + (topK || 10))
        .then(function(r) { return r.json(); });
    },
    violations: function(q, topK) {
      return fetch(AuditAPI.base + '/api/audit/search/violations?q=' + encodeURIComponent(q) + '&top_k=' + (topK || 10))
        .then(function(r) { return r.json(); });
    }
  },

  // ── 后台任务 (Phase 5) ──
  tasks: {
    list: function(params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(AuditAPI.base + '/api/audit/tasks?' + qs).then(function(r) { return r.json(); });
    },
    get: function(id) {
      return fetch(AuditAPI.base + '/api/audit/tasks/' + id).then(function(r) { return r.json(); });
    },
    create: function(data) {
      return fetch(AuditAPI.base + '/api/audit/tasks', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 文书生成 (Phase 6) ──
  documents: {
    generate: function(data) {
      return fetch(AuditAPI.base + '/api/audit/documents/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    },
    batch: function(data) {
      return fetch(AuditAPI.base + '/api/audit/documents/batch', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    }
  },

  // ── Agent 管理 ──
  agents: {
    list: function() {
      return fetch(AuditAPI.base + '/api/audit/agents').then(function(r) { return r.json(); });
    },
    create: function(data) {
      return fetch(AuditAPI.base + '/api/audit/agents', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    },
    update: function(name, data) {
      return fetch(AuditAPI.base + '/api/audit/agents/' + name, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    },
    delete: function(name) {
      return fetch(AuditAPI.base + '/api/audit/agents/' + name, {method: 'DELETE'}).then(function(r) { return r.json(); });
    }
  },

  // ── AI 对话 (通过 OpenSquilla) ──
  chat: {
    send: function(message, sessionId) {
      return fetch(AuditAPI.base + '/api/chat', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: message, session_id: sessionId})
      }).then(function(r) { return r.json(); });
    },
    history: function(sessionId) {
      return fetch(AuditAPI.base + '/api/chat/history?session_id=' + sessionId).then(function(r) { return r.json(); });
    }
  }
};

// 兼容旧代码 — Phase 7 修复: 对接真实 MinIO + MySQL 项目
var MinioAPI = {
  base: window.location.origin,
  listFiles: function(project) {
    // project 可能是 MySQL UUID 或 MinIO 文件夹名
    return fetch(AuditAPI.base + '/api/audit/workspace/files?project=' + encodeURIComponent(project))
      .then(function(r) { return r.json(); });
  },
  upload: function(project, file) {
    var form = new FormData();
    form.append('file', file);
    return fetch(AuditAPI.base + '/api/audit/projects/' + encodeURIComponent(project) + '/upload', {
      method: 'POST', body: form
    }).then(function(r) { return r.json(); });
  },
  getDownloadUrl: function(project, filename) {
    return fetch(AuditAPI.base + '/api/audit/workspace/download?project=' + encodeURIComponent(project) + '&file=' + encodeURIComponent(filename))
      .then(function(r) { return r.json(); });
  },
  deleteFile: function(project, filename) {
    return fetch(AuditAPI.base + '/api/audit/workspace/delete?project=' + encodeURIComponent(project) + '&file=' + encodeURIComponent(filename), { method: 'DELETE' })
      .then(function(r) { return r.json(); });
  },
  listProjects: function() {
    return fetch(AuditAPI.base + '/api/audit/workspace/projects').then(function(r) { return r.json(); });
  }
};
