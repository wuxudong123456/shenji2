"""LiteParse 文档 Markdown 化服务 — 本地解析器，不做 OCR（OCR 走 MinerU）"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import tempfile
import os

app = Flask(__name__)
CORS(app)

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'engine': 'liteparse', 'role': 'md-converter', 'version': '2.4.0', 'endpoints': ['POST /parse', 'POST /md']})

@app.route('/parse', methods=['POST'])
def parse():
    """文档结构化解析 — 返回 text + 坐标，不做 OCR"""
    if 'file' not in request.files:
        return jsonify({'error': '请上传文件'}), 400

    f = request.files['file']
    suffix = os.path.splitext(f.filename)[1] if f.filename else '.pdf'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.save(tmp.name)
    tmp.close()

    try:
        from liteparse import LiteParse
        # 不做 OCR — OCR 是 MinerU 的职责
        parser = LiteParse(ocr_enabled=False)
        result = parser.parse(tmp.name)
        os.unlink(tmp.name)

        text_items = []
        for page in result.pages:
            for item in page.text_items:
                text_items.append(item)

        return jsonify({
            'success': True,
            'fields': [{'name': item.text, 'bbox': [item.x, item.y, item.width, item.height]} for item in text_items[:50]],
            'text': result.text[:5000] if result.text else '',
            'pages': len(result.pages),
            'metadata': {'filename': f.filename, 'engine': 'liteparse'}
        })
    except Exception as e:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        return jsonify({'error': str(e)}), 500

@app.route('/md', methods=['POST'])
def to_markdown():
    """文档 → Markdown 转换（liteparse 核心能力）"""
    if 'file' not in request.files:
        return jsonify({'error': '请上传文件'}), 400

    f = request.files['file']
    suffix = os.path.splitext(f.filename)[1] if f.filename else '.pdf'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.save(tmp.name)
    tmp.close()

    try:
        from liteparse import LiteParse
        parser = LiteParse(ocr_enabled=False)
        result = parser.parse(tmp.name)
        os.unlink(tmp.name)

        pages_md = []
        all_text_items = []
        for page in result.pages:
            md_text = getattr(page, 'markdown', None)
            if not md_text:
                md_text = page.text  # fallback for liteparse < v2.4.0
            pages_md.append({
                'page': page.page_num,
                'markdown': md_text[:10000] if md_text else '',
                'text': page.text[:5000] if page.text else ''
            })
            for item in page.text_items:
                all_text_items.append({
                    'page': page.page_num,
                    'text': item.text,
                    'x': round(item.x, 1), 'y': round(item.y, 1),
                    'w': round(item.width, 1), 'h': round(item.height, 1),
                    'confidence': round(getattr(item, 'confidence', 1.0), 2),
                })

        return jsonify({
            'success': True,
            'full_markdown': result.text[:20000] if result.text else '',
            'pages': pages_md,
            'text_items': all_text_items,
            'metadata': {'filename': f.filename, 'engine': 'liteparse', 'pages': len(result.pages)}
        })
    except Exception as e:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5006)
