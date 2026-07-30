"""AuditWorkbench Flask API — MinIO 集成"""
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from config import Config
from services.minio_client import (
    upload_file, download_file, get_presigned_url,
    list_objects, list_folders, delete_object, get_object_info
)
import os
import io
from urllib.parse import unquote

app = Flask(__name__)
app.config.from_object(Config)
CORS(app)

# 强制 UTF-8 响应
app.config['JSON_AS_ASCII'] = False


# ===== 文件管理 API =====

@app.route('/api/files/<path:project_name>/upload', methods=['POST'])
def api_upload(project_name):
    """上传文件到项目文件夹"""
    project_name = unquote(project_name)
    if 'file' not in request.files:
        return jsonify({'error': '请选择文件'}), 400

    f = request.files['file']
    filename = f.filename
    # 确保文件名正确编码
    object_path = f"{project_name}/{filename}"

    try:
        upload_file(f.read(), object_path, f.content_type or 'application/octet-stream')
        return jsonify({
            'success': True,
            'path': object_path,
            'message': f'{f.filename} 已上传到 {project_name}'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/<path:project_name>/list', methods=['GET'])
def api_list(project_name):
    """列出项目文件夹下的文件"""
    project_name = unquote(project_name)
    prefix = project_name + '/'
    objects = list_objects(prefix)
    # 构建目录树
    files = []
    for obj in objects:
        name = obj['name'].replace(prefix, '', 1)
        if '/' in name:
            continue  # 跳过子目录
        files.append({
            'name': name,
            'size': obj['size'],
            'last_modified': obj['last_modified'],
            'path': obj['name']
        })
    return jsonify({'success': True, 'project': project_name, 'files': files})


@app.route('/api/files/<path:project_name>/download/<path:filename>', methods=['GET'])
def api_download(project_name, filename):
    """下载/预览文件"""
    project_name = unquote(project_name)
    object_path = f"{project_name}/{filename}"
    # 生成预签名 URL 重定向
    url = get_presigned_url(object_path, expires=3600)
    return jsonify({'success': True, 'url': url})


@app.route('/api/files/<path:project_name>/delete/<path:filename>', methods=['DELETE'])
def api_delete(project_name, filename):
    """删除文件"""
    project_name = unquote(project_name)
    object_path = f"{project_name}/{filename}"
    delete_object(object_path)
    return jsonify({'success': True, 'message': f'{filename} 已删除'})


@app.route('/api/files/<project_name>/info/<path:filename>', methods=['GET'])
def api_info(project_name, filename):
    """获取文件元数据"""
    object_path = f"{project_name}/{filename}"
    info = get_object_info(object_path)
    return jsonify({'success': True, 'info': info})


# ===== 项目管理 API =====

@app.route('/api/projects', methods=['GET'])
def api_projects():
    """列出所有项目文件夹"""
    folders = list_folders()
    projects = []
    for folder in folders:
        # 统计文件夹内文件数
        objects = list_objects(folder + '/')
        total_size = sum(o['size'] for o in objects)
        projects.append({
            'name': folder,
            'file_count': len(objects),
            'total_size': total_size
        })
    return jsonify({'success': True, 'projects': projects})


@app.route('/api/projects', methods=['POST'])
def api_create_project():
    """创建项目文件夹"""
    data = request.get_json()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': '项目名称不能为空'}), 400
    # MinIO 中创建文件夹：上传零字节对象，以 / 结尾
    upload_file(b'', f"{name}/", 'application/x-directory')
    return jsonify({'success': True, 'name': name})


@app.route('/api/projects/<path:name>/rename', methods=['POST'])
def api_rename(name):
    """重命名文件夹"""
    name = unquote(name)
    data = request.get_json()
    new_name = data.get('new_name', '').strip()
    if not new_name:
        return jsonify({'error': '新名称不能为空'}), 400
    # 复制所有文件到新目录，删除旧目录
    objects = list_objects(name + '/')
    for obj in objects:
        old_path = obj['name']
        new_path = new_name + '/' + old_path[len(name)+1:]
        content = download_file(old_path)
        upload_file(content, new_path)
        delete_object(old_path)
    return jsonify({'success': True, 'new_name': new_name})


@app.route('/api/projects/<path:name>/delete', methods=['DELETE'])
def api_delete_project(name):
    """删除文件夹及其所有文件"""
    name = unquote(name)
    objects = list_objects(name + '/')
    for obj in objects:
        delete_object(obj['name'])
    # 删除文件夹标记
    try: delete_object(name + '/')
    except: pass
    return jsonify({'success': True, 'message': name + ' 已删除'})


# ===== OCR 解析 =====

@app.route('/api/ocr/parse', methods=['POST'])
def api_ocr_parse():
    """上传文件进行 OCR 解析"""
    if 'file' not in request.files:
        return jsonify({'error': '请选择文件'}), 400

    f = request.files['file']
    # 保存临时文件
    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
    f.save(tmp.name)
    tmp.close()

    try:
        from services.ocr_client import OCREngine
        result = OCREngine.parse(tmp.name)
        result['filename'] = f.filename
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        os.unlink(tmp.name)


@app.route('/api/ocr/health', methods=['GET'])
def api_ocr_health():
    """检查 OCR 引擎状态"""
    from services.ocr_client import OCREngine
    engine = OCREngine.get_engine()
    return jsonify({
        'engine': engine.__class__.__name__,
        'healthy': engine.health()
    })


# ===== 模板管理 API =====


@app.route('/api/templates/categories', methods=['GET'])
def api_template_categories():
    """获取模板分类树 — 轻量版（只含名称和数量，不含字段详情）"""
    from services.template_service import _load_all

    full = request.args.get('full', '0')  # full=1 返回完整树含模板列表
    all_templates = _load_all()
    domains: dict = {}

    for name, tmpl in all_templates.items():
        domain = tmpl.get("domain", "general")
        tc = tmpl.get("template_class", {})
        category = tc.get("category", "其他")
        if domain not in domains:
            domains[domain] = {}
        if category not in domains[domain]:
            domains[domain][category] = 0
        domains[domain][category] += 1

    tree = []
    for domain in sorted(domains.keys()):
        cats = [{"name": c, "count": n} for c, n in sorted(domains[domain].items())]
        tree.append({
            "domain": domain,
            "categories": cats,
            "total": sum(c["count"] for c in cats),
        })

    result = {"success": True, "tree": tree, "total": len(all_templates)}

    if full == '1':
        from services.template_service import list_categories, get_stats
        result["tree_full"] = list_categories()
        result["stats"] = get_stats()

    return jsonify(result)


@app.route('/api/templates', methods=['GET'])
def api_templates():
    """列出/搜索模板"""
    domain = request.args.get('domain', '')
    category = request.args.get('category', '')
    query = request.args.get('q', '')
    limit = request.args.get('limit', 50, type=int)

    from services.template_service import list_templates, search_templates
    if query:
        results = search_templates(query, limit=limit)
    else:
        results = list_templates(domain=domain or None, category=category or None)

    return jsonify({
        'success': True,
        'templates': results,
        'count': len(results)
    })


@app.route('/api/templates/<path:name>', methods=['GET'])
def api_template_detail(name):
    """获取单个模板详情（含字段定义 + 违规表达式）"""
    from urllib.parse import unquote
    from services.template_service import get_template
    name = unquote(name)
    tmpl = get_template(name)
    if not tmpl:
        return jsonify({'error': '模板不存在: ' + name}), 404

    def _clean(s):
        """移除控制字符，避免 JSON 序列化异常"""
        if not isinstance(s, str):
            return s
        return ''.join(c for c in s if ord(c) >= 32 or c in '\n\r\t')

    return jsonify({
        'success': True,
        'template': {
            'name': tmpl.get('name'),
            'description': _clean(tmpl.get('description', '')),
            'domain': tmpl.get('domain'),
            'tags': tmpl.get('tags', []),
            'guideline': _clean(tmpl.get('guideline', '')),
            'template_class': tmpl.get('template_class'),
            'fields': tmpl.get('output', {}).get('fields', []),
            'violations': [
                {
                    'expression': _clean(v.get('expression', '')),
                    'description': _clean(v.get('description', '')),
                    'audit_item': _clean(v.get('audit_item', '')),
                    'suspicion': _clean(v.get('suspicion', '')),
                    'regulation': _clean(v.get('regulation', '')),
                }
                for v in tmpl.get('violations', [])
            ]
        }
    })


# ===== SKU 提取 API =====


@app.route('/api/templates/classify', methods=['POST'])
def api_template_classify():
    """自动分类文档 → 匹配最佳模板（不提取字段）"""
    data = request.get_json()
    if not data or not data.get('markdown'):
        return jsonify({'error': '请提供 markdown 文本'}), 400

    from services.extraction_service import classify_document
    result = classify_document(data['markdown'])
    return jsonify(result)


@app.route('/api/templates/extract', methods=['POST'])
def api_template_extract():
    """根据模板从 Markdown 中提取结构化字段"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '请提供 JSON body'}), 400

    template_name = data.get('template_name', '')
    markdown = data.get('markdown', '')
    auto = data.get('auto', False)

    if not markdown:
        return jsonify({'error': '请提供 markdown 文本'}), 400

    from services.extraction_service import extract_fields, auto_classify_and_extract

    if auto:
        result = auto_classify_and_extract(markdown)
    elif template_name:
        result = extract_fields(template_name, markdown)
    else:
        return jsonify({'error': '请提供 template_name 或设置 auto=true'}), 400

    return jsonify(result)


@app.route('/api/llm/health', methods=['GET'])
def api_llm_health():
    """检查 LLM 服务状态"""
    from services.llm_client import health
    return jsonify({'llm_available': health()})


# ===== 健康检查 =====

@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({'status': 'ok', 'minio': Config.MINIO_ENDPOINT})


# 注册审计业务路由 (Phase 4.5)
from routes.audit_routes import register_audit_routes
register_audit_routes(app)

# 注册后台任务路由 (Phase 5)
from routes.task_routes import register_task_routes
register_task_routes(app)

# 注册增强功能路由 (Phase 6)
from routes.phase6_routes import register_phase6_routes
register_phase6_routes(app)

# 注册 Agent 管理路由 (Phase 3 扩展)
from routes.agent_routes import register_agent_routes
register_agent_routes(app)

# ---- 前端静态文件服务 ----
import os as _os
_FRONTEND_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'frontend')

# CSS / JS / 图片 / 字体
@app.route('/css/<path:filename>')
@app.route('/js/<path:filename>')
@app.route('/lib/<path:filename>')
def serve_static(filename):
    from flask import send_from_directory
    import os as _os2
    subdir = request.path.split('/')[1]
    return send_from_directory(_os2.path.join(_FRONTEND_DIR, subdir), filename)

# HTML 页面
@app.route('/')
@app.route('/<page>.html')
def serve_page(page='index'):
    from flask import send_from_directory
    filename = page + '.html'
    path = _os.path.join(_FRONTEND_DIR, filename)
    if _os.path.isfile(path):
        return send_from_directory(_FRONTEND_DIR, filename)
    return send_from_directory(_FRONTEND_DIR, 'index.html')

if __name__ == '__main__':
    app.run(host=Config.FLASK_HOST, port=Config.FLASK_PORT, debug=True)
