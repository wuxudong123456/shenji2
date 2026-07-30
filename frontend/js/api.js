/**
 * AuditWorkbench — API 客户端
 * 后端: OpenSquilla 网关 (同源)
 */
var AuditAPI = {
  base: window.location.origin,

  // ── 项目管理 ──
  projects: {
    list: function() {
      return fetch(this.base + '/api/audit/projects').then(function(r) { return r.json(); });
    },
    create: function(data) {
      return fetch(this.base + '/api/audit/projects', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    },
    get: function(id) {
      return fetch(this.base + '/api/audit/projects/' + id).then(function(r) { return r.json(); });
    },
    delete: function(id) {
      return fetch(this.base + '/api/audit/projects/' + id, {method: 'DELETE'}).then(function(r) { return r.json(); });
    }
  },

  // ── 文件上传 + OCR ──
  files: {
    upload: function(projectId, file, onProgress) {
      var form = new FormData();
      form.append('file', file);
      return fetch(this.base + '/api/audit/projects/' + projectId + '/upload', {
        method: 'POST', body: form
      }).then(function(r) { return r.json(); });
    },
    list: function(projectId) {
      return fetch(this.base + '/api/audit/projects/' + projectId + '/files').then(function(r) { return r.json(); });
    },
    trace: function(docId) {
      return fetch(this.base + '/api/audit/documents/' + docId + '/trace').then(function(r) { return r.json(); });
    },
    reparse: function(data) {
      return fetch(this.base + '/api/audit/documents/reparse', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 数据工坊 ──
  data: {
    tables: function(projectId) {
      return fetch(this.base + '/api/audit/projects/' + projectId + '/data').then(function(r) { return r.json(); });
    },
    rows: function(table, params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(this.base + '/api/audit/data/' + table + '/rows?' + qs).then(function(r) { return r.json(); });
    },
    smartQuery: function(question, projectId) {
      return fetch(this.base + '/api/audit/data/query', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question: question, project_id: projectId})
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 知识工坊 ──
  knowledge: {
    violations: function(params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(this.base + '/api/audit/knowledge/violations?' + qs).then(function(r) { return r.json(); });
    },
    regulations: function(params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(this.base + '/api/audit/knowledge/regulations?' + qs).then(function(r) { return r.json(); });
    },
    regulationGraph: function(lawId) {
      return fetch(this.base + '/api/audit/knowledge/regulation/' + lawId + '/graph').then(function(r) { return r.json(); });
    },
    clauses: function(lawId) {
      return fetch(this.base + '/api/audit/knowledge/clauses/' + lawId).then(function(r) { return r.json(); });
    }
  },

  // ── 违规表达式 ──
  expression: {
    execute: function(expression, projectId, table) {
      return fetch(this.base + '/api/audit/expression/execute', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({expression: expression, project_id: projectId, table: table || 'data_contracts'})
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 疑点报告 ──
  suspicion: {
    generate: function(data) {
      return fetch(this.base + '/api/audit/suspicion/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 模板 ──
  templates: {
    list: function(params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(this.base + '/api/audit/templates?' + qs).then(function(r) { return r.json(); });
    }
  },

  // ── 智能分析工作流 ──
  analysis: {
    create: function(intent, projectId) {
      return fetch(this.base + '/api/audit/analysis', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({intent: intent, project_id: projectId || ''})
      }).then(function(r) { return r.json(); });
    },
    get: function(taskId) {
      return fetch(this.base + '/api/audit/analysis/' + taskId).then(function(r) { return r.json(); });
    },
    step: function(taskId, stepNum, data) {
      return fetch(this.base + '/api/audit/analysis/' + taskId + '/step/' + stepNum, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data || {})
      }).then(function(r) { return r.json(); });
    },
    confirm: function(taskId, data) {
      return fetch(this.base + '/api/audit/analysis/' + taskId + '/confirm', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data || {})
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 案例库 (Phase 6) ──
  cases: {
    list: function(params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(this.base + '/api/audit/cases?' + qs).then(function(r) { return r.json(); });
    },
    get: function(id) {
      return fetch(this.base + '/api/audit/cases/' + id).then(function(r) { return r.json(); });
    },
    create: function(data) {
      return fetch(this.base + '/api/audit/cases', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 语义搜索 (Phase 6) ──
  search: {
    laws: function(q, topK) {
      return fetch(this.base + '/api/audit/search/laws?q=' + encodeURIComponent(q) + '&top_k=' + (topK || 10))
        .then(function(r) { return r.json(); });
    },
    violations: function(q, topK) {
      return fetch(this.base + '/api/audit/search/violations?q=' + encodeURIComponent(q) + '&top_k=' + (topK || 10))
        .then(function(r) { return r.json(); });
    }
  },

  // ── 后台任务 (Phase 5) ──
  tasks: {
    list: function(params) {
      var qs = new URLSearchParams(params || {}).toString();
      return fetch(this.base + '/api/audit/tasks?' + qs).then(function(r) { return r.json(); });
    },
    get: function(id) {
      return fetch(this.base + '/api/audit/tasks/' + id).then(function(r) { return r.json(); });
    },
    create: function(data) {
      return fetch(this.base + '/api/audit/tasks', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    }
  },

  // ── 文书生成 (Phase 6) ──
  documents: {
    generate: function(data) {
      return fetch(this.base + '/api/audit/documents/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    },
    batch: function(data) {
      return fetch(this.base + '/api/audit/documents/batch', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    }
  },

  // ── Agent 管理 ──
  agents: {
    list: function() {
      return fetch(this.base + '/api/audit/agents').then(function(r) { return r.json(); });
    },
    create: function(data) {
      return fetch(this.base + '/api/audit/agents', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    },
    update: function(name, data) {
      return fetch(this.base + '/api/audit/agents/' + name, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
      }).then(function(r) { return r.json(); });
    },
    delete: function(name) {
      return fetch(this.base + '/api/audit/agents/' + name, {method: 'DELETE'}).then(function(r) { return r.json(); });
    }
  },

  // ── AI 对话 (通过 OpenSquilla) ──
  chat: {
    send: function(message, sessionId) {
      return fetch(this.base + '/api/chat', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: message, session_id: sessionId})
      }).then(function(r) { return r.json(); });
    },
    history: function(sessionId) {
      return fetch(this.base + '/api/chat/history?session_id=' + sessionId).then(function(r) { return r.json(); });
    }
  }
};

// 兼容旧代码
var MinioAPI = {
  base: window.location.origin,
  listFiles: function(project) {
    return AuditAPI.files.list(project);
  },
  upload: function(project, file) {
    return AuditAPI.files.upload(project, file);
  },
  getDownloadUrl: function(project, filename) {
    // 通过 MinIO 直接 URL
    return Promise.resolve({url: window.location.origin + '/api/audit/documents/download?project=' + project + '&file=' + encodeURIComponent(filename)});
  },
  deleteFile: function(project, filename) {
    return Promise.resolve({ok: true}); // 暂不实现删除
  },
  listProjects: function() {
    return AuditAPI.projects.list();
  }
};
