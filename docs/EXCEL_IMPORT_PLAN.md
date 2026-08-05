# Excel 数据导入方案

> **源文件**: `2026-6-20提取模板.xlsx`  
> **目标数据库**: `tt` (192.168.3.164:3306)  
> **方案版本**: v1.1（经可行性审查修正）  
> **日期**: 2026-08-04

---

## 一、源数据概览

| 属性 | 值 |
|------|-----|
| Sheet | 审计疑点特征提取 |
| 数据行数 | 2231 |
| 列数 | 15 |
| 含案例行数 | 2152（79 行无案例） |
| 案例对象总数 | 约 2634 |
| 分类数 | 12 |

### 分类分布

| 数量 | 分类 |
|------|------|
| 650 | 业务类-农业农村审计 |
| 419 | 业务类-其他 |
| 414 | 业务类-部门预算执行审计 |
| 319 | 业务类-国有企业审计 |
| 296 | 业务类-社会保障审计 |
| 104 | 业务类-固定资产投资审计 |
| 10 | 效率类-乡村振兴审计 |
| 8 | 效率类- |
| 4 | 业务类-资源环境审计 |
| 3 | 业务类-财政审计 |
| 3 | 研究类- |
| 1 | 研究类-乡村振兴审计 |

### 15 列清单

| 列号 | 列名 | 内容形态 | 用途 |
|------|------|---------|------|
| ① | 原始行号 | 整数 | 生成 violation_code |
| ② | 审计事项名称 | 短文本 | 归入 description |
| ③ | 审计事项分类 | `业务类-部门预算执行审计` 等 | → category_path |
| ④ | 常见表现及形式 | 多行文本 | 归入 description |
| ⑤ | 审计所需数据 | JSON（files + tables） | → **required_data（新字段）** |
| ⑥ | 审计方法步骤 | Markdown 长文 | → **audit_procedure（新字段）** |
| ⑦ | 审计疑点名称 | 短文本 | → violation_title |
| ⑧ | 所需资料类型 | 逗号分隔标签 | 归入 description |
| ⑨ | 对应数据字段 | 结构化定义文本 | 追加到 expression_text |
| ⑩ | 违规表达式 | 伪SQL | → expression_text |
| ⑪ | 表达式备注 | 短文本 | 追加到 expression_text |
| ⑫ | 疑点发现方法 | 结构化文本 | 归入 description |
| ⑬ | 违规依据 | JSON 数组 | 格式化为《法规名》追加到 description；同时导入 audit_violation_law_refs |
| ⑭ | 疑点推理 | 短文 | 归入 description |
| ⑮ | 典型审计案例 | JSON 数组 | → audit_cases + 关联表 |

---

## 二、数据库变更

### 2.1 audit_violations 加两个字段

```sql
ALTER TABLE tt.audit_violations
  ADD COLUMN audit_procedure MEDIUMTEXT 
    COMMENT '审计方法步骤（Markdown）' AFTER expression_text,
  ADD COLUMN required_data JSON 
    COMMENT '审计所需数据（files+tables结构定义）' AFTER audit_procedure;
```

> **兼容性说明**：
> - 列表 API（`_VIOLATION_LIST_COLS`）使用显式列名，不包含新字段 → 列表响应不变
> - 详情 API（`get_violation_detail()`）使用 `SELECT *` → 自动包含新字段
> - 所有现有 INSERT 使用显式列名 → 不受影响

### 2.2 清空旧数据

```sql
-- 先删 cases（级联到 audit_case_violations + audit_case_law_refs）
DELETE FROM tt.audit_cases;
-- 再删 violations（级联到 audit_violation_law_refs + 剩余 audit_case_violations）
DELETE FROM tt.audit_violations;
```

> **为什么替换而不是追加**：旧数据来自 YAML 模板（文档级违规，几百条，仅 4 个业务字段），新数据来自 Excel（审计疑点级违规，2231 条，15 列完整知识体系）。两套数据分类体系不同、表达式表体系不同、内容维度不同。混合存放导致前端分类混乱。

### 2.3 删除 FAISS 缓存

```
删除文件: backend/data/.vector_cache/violation_index.pkl
```

> 重启后端时 `vector_store.py` 检测缓存不存在，从新数据自动重建。

### 2.4 删除死表（可选）

```sql
DROP TABLE IF EXISTS tt.audit_templates;      -- YAML 是唯一模板源，DB 表从未被代码读写
DROP TABLE IF EXISTS tt.project_suspicions;   -- 整个 Python 代码 0 次引用
DROP TABLE IF EXISTS tt.audit_agent_traces;   -- 整个 Python 代码 0 次引用
```

---

## 三、字段映射（v2 修正版）

### 3.1 Excel → audit_violations

| 目标字段 | 来源 | 处理逻辑 |
|---------|------|---------|
| `violation_code` | ①原始行号+疑点序号 | `XV-{YYYYMMDD}-{行号:04d}-{序号:02d}`，如 `XV-20260620-0006-01`（YYYYMMDD=批次日期 20260620） |
| `violation_title` | ⑦审计疑点名称 | 直接映射，截断至 500 字符 |
| `audititem_id` | ②审计事项名称/③分类 | **A+B 匹配策略**：先名称匹配树 name，失败回退分类匹配树节点。实测 85% |
| `category_path` | ③审计事项分类 | 直接映射，如 `业务类-部门预算执行审计` |
| `severity` | - | 默认 `medium` |
| `expression_text` | ⑩违规表达式 | **只存纯表达式**，不拼接 ⑪⑨ |
| `audit_procedure` | ⑥+⑫ | `# 审计方法步骤` + `# 疑点发现方法` 合并 |
| `required_data` | ⑤+⑧+⑨ | 丰富 JSON（files/tables 含 material_type + fields） |
| `description` | ⑪+⑭+⑬法规名 | 格式见 3.1.2 |
| `source_file` | - | `2026-6-20提取模板.xlsx` |
| `import_batch` | - | `EXCEL-{YYYYMMDD-HHMM}` |
| `is_reviewed` | - | 0 |
| `review_status` | - | `mapped`（audititem_id 匹配成功）或 `pending_mapping`（失败） |
| `creator` | - | `system` |

#### 3.1.1 audititem_id 匹配（A+B 组合，实测 85%）

```
A. 审计事项名称 → 树 name:
   A1 完全相等 → A2 归一化相等 → A3 Excel名含树名 → A4 树名含Excel名
B. 分类回退 → 树节点:
   B1 分类最后一段 == 树 name → B2 分类段在 path_names，取 level 最小节点
失败: audititem_id = NULL, review_status = pending_mapping（不随意匹配）
```

> **实测**：Excel 审计事项名称与树 name 直接匹配仅 1%（命名体系不同："专项资金长期闲置未发挥效益" vs "基本支出部门预算执行审查"）。分类回退（Col③ 最后一段如"部门预算执行审计"匹配树节点）达到 85%。「国有企业审计」「资源环境审计」在树中无对应节点（树用"企业审计"/"自然资源和生态环境审计"），归入 pending_mapping。

#### 3.1.2 description 格式（规则说明最前，法规名保留）

```
【规则说明】
{Col⑪ 表达式备注}

【疑点推理】
{Col⑭ 疑点推理}

【违规依据】
• 《法规名称》（文号）条款号
```

> **为什么法规名必须用《》**：前端 `knowledge.js` 用 `desc.match(/《[^》]+》/g)` 从 description 提取法规名展示在违规卡片上。不加《》→ 正则匹配为空 → 全部显示「待关联法规」。
>
> **从 description 移除**（v2）：审计方法步骤、所需资料类型、对应数据字段、完整法规 JSON、典型案例、原始表达式。这些已有独立字段或关联表。

#### 3.1.3 required_data 丰富 JSON

```json
{
  "files": [
    {"num": "1", "filename": "采购合同及补充协议",
     "material_type": "合同协议类-采购合同",
     "fields": ["合同编号", "合同金额", "采购方式", "供应商名称"]}
  ],
  "tables": [
    {"num": "1", "tablename": "采购合同台账",
     "material_type": "登记台账类-台账",
     "fields": ["合同编号", "合同金额", "采购方式", "签订日期"]}
  ]
}
```

> 合并逻辑：Col⑤ files/tables 名称 + Col⑧ 所需资料类型（`大类-子类-名称` → material_type）+ Col⑨ 对应数据字段（`表名{字段、字段}` → fields）。实测 fields 填充率约 20%（源数据 Col⑨ 部分行缺失/格式多样），material_type 填充率更高。

### 3.2 Excel → audit_violation_law_refs

Col⑬ JSON 数组逐元素映射为关联记录：

```python
for law in col13_json:
    law_title = law["法规名称"]
    law_id = _match_law_id(law_title)  # 复用 migrate_violation_law_refs.py 的 5 级降级匹配
    clause_ref = law["条款号"]
    INSERT INTO audit_violation_law_refs (violation_id, law_id, law_title, clause_ref)
```

> 匹配不上也写入（`law_id=NULL`，`law_title` 保留），后续可人工补匹配。

### 3.3 Excel → audit_cases

Col⑮ JSON 数组的每个案例对象映射为一条案例记录：

| 目标字段 | 来源 | 处理 |
|---------|------|------|
| `title` | 案例简述 | 截取前 500 字符 |
| `domain` | 所属违规的 category_path | 如 `业务类-部门预算执行审计` |
| `case_summary` | 案例简述 | 完整文本 |
| `audit_method` | 核查方法 | 完整文本 |
| `audit_finding` | 违规表现 | 完整文本 |
| `audit_impact` | 风险影响 | 完整文本 |
| `involved_amount` | - | NULL（Excel 案例无金额字段） |
| `source` | - | `2026-6-20提取模板.xlsx` |

### 3.4 关联表

```python
# 案例 ↔ 违规
INSERT INTO audit_case_violations (case_id, violation_id) VALUES (...)

# 案例 ↔ 法规（继承所属违规的法规关联）
INSERT INTO audit_case_law_refs (case_id, law_id) VALUES (...)
```

---

## 四、代码修改清单

### 4.1 新建文件

| 文件 | 用途 | 优先级 |
|------|------|--------|
| `backend/data/import_excel.py` | Excel 导入脚本 | **P0** |

### 4.2 修改文件

| 文件 | 改动内容 | 行数 | 优先级 |
|------|---------|------|--------|
| `backend/services/knowledge_service.py` | `search_violations()` 加 `category` 参数，支持按分类筛选 | ~5 | **P1** |
| `backend/routes/audit_routes.py` | ①列表 API 加 `category` 查询参数；②**新增** `GET /api/audit/knowledge/violations/<id>` 详情端点 | ~15 | **P1** |
| `frontend/js/knowledge.js` | ①违规面板顶部加分类下拉框（`<select>`）；②`showViolationDetail()` 改为异步拉取详情，新增「审计方法步骤」+「审计所需数据」两张卡片 | ~50 | **P1** |

### 4.3 不改的文件（自动适配）

| 文件 | 原因 |
|------|------|
| `backend/routes/phase6_routes.py` | 案例 CRUD 字段不变 |
| `backend/services/vector_store.py` | 删缓存后自动重建 |
| `backend/services/execution_planner.py` | 从 DB 读表达式，数据换了逻辑不变 |
| `backend/services/expression_engine.py` | 同上 |
| `backend/agents/audit_analyzer.py` | 通过 API 间接查询 |
| `backend/mcp_servers/knowledge_mcp.py` | 通过 service 层间接查询 |
| `frontend/js/analysis.js` | 字段映射不变 |
| `frontend/js/analysis-wiz.js` | 字段映射不变 |

---

## 五、导入执行步骤

```
Step 1  备份旧数据                  ── mysqldump 或 SELECT INTO OUTFILE
Step 2  ALTER TABLE 加两列          ── SQL（2.1）
Step 3  DELETE 清空旧数据           ── SQL，先 cases 后 violations（2.2）
Step 4  删 FAISS 缓存              ── 手动删 pkl 文件（2.3）
Step 5  删死表                     ── SQL（2.4，可选）
Step 6  运行 import_excel.py --dry  ── 试运行，验证解析和匹配率
Step 7  运行 import_excel.py --run  ── 正式导入
Step 8  改 knowledge_service.py    ── 加 category 参数（P1）
Step 9  改 audit_routes.py         ── 加 category + 详情端点（P1）
Step 10 改 knowledge.js            ── 加下拉框 + 详情卡片（P1）
Step 11 重启后端                   ── FAISS 自动重建
Step 12 验收                       ── 按第七章清单检查
```

---

## 六、注意事项 & 风险

### ⚠️ 关键注意

| # | 注意点 | 说明 |
|---|--------|------|
| 1 | **法规名必须加《》** | 导入脚本格式化 Col⑬ 时以 `《法规名》` 写入 description，否则前端正则全部失败 |
| 2 | **法规名去重包裹** | 若 Col⑬ 原始名称已含《》（如 `《预算法》`），导入脚本检测后不再重复包裹 |
| 3 | **外键级联** | DELETE `audit_violations` 和 `audit_cases` 会自动清空 4 张关联表 |
| 4 | **FAISS 缓存必须删** | 不删则向量搜索仍指向旧数据 |
| 5 | **expression_text 去重** | 导入脚本用 Col⑩ 原始表达式（拼接前）做 SELECT 去重检查 |
| 6 | **`audititem_id` 留 NULL** | Excel 分类与 `sys_audititem_SLFF` 体系不同，暂不匹配 |
| 7 | **案例无独立法规** | Excel 案例对象不含法规字段，`audit_case_law_refs` 从所属违规继承 |
| 8 | **79 行无案例** | Col⑮ 为空或 `[]` 时跳过案例创建，不影响违规导入 |
| 9 | **DELETE 不可逆** | 执行前必须备份 |

### 🟡 功能行为变化

| 变化 | 影响 | 缓解 |
|------|------|------|
| 分类体系切换 | 旧 `audit/合同协议类/...` → 12 个中文审计分类 | 正面变化 |
| 表达式执行 | 目标表不存在，执行失败 | 该功能在 Phase 4 完成前本就不工作，无退步 |
| 违规数量暴增 | 2231 条 vs 原来几百条 | 加分类下拉框，每个分类最多 650 条，per_page=200 够用 |
| 历史分析任务 | 旧 violation_id 失效 | 旧任务不可逆，不影响新任务 |
| `v.cases` 仍为 0 | 前端硬编码 | 后续可改为关联查询 COUNT，本方案不改 |
| `v.materials` 仍为空 | 前端硬编码 | Col⑧ 资料类型已在 description 中可读 |

### 🔴 不可逆操作

- `DELETE FROM audit_violations` 和 `DELETE FROM audit_cases` 不可逆。执行前备份：
  ```bash
  mysqldump tt audit_violations audit_cases audit_violation_law_refs \
            audit_case_violations audit_case_law_refs > backup_before_excel_import.sql
  ```

---

## 七、导入后验证清单

- [ ] 违规列表加载正常，下拉框 12 个分类可切换
- [ ] 违规卡片显示法规名（不是「待关联法规」）
- [ ] 点击违规卡片 → 弹窗含「审计方法步骤」卡片（Markdown 渲染）
- [ ] 点击违规卡片 → 弹窗含「审计所需数据」卡片（JSON 展开）
- [ ] 案例列表从 5 条变为 2600+ 条
- [ ] 案例通过关联表可查到对应违规
- [ ] FAISS 语义搜索违规返回新数据
- [ ] 七步分析流程可选择新违规模型
- [ ] `audit_violation_law_refs` 有关联记录
- [ ] `audit_case_violations` 有关联记录

---

## 八、优先级排序

```
P0 ─ 必须做，阻塞整个导入
    ├── import_excel.py 导入脚本
    ├── ALTER TABLE 加两列
    ├── DELETE 清旧数据 + 备份
    └── 删 FAISS 缓存

P1 ─ 导入后前端才完整可用
    ├── knowledge_service.py 加 category 参数
    ├── audit_routes.py 加详情端点 + category 参数
    └── knowledge.js 加下拉框 + 详情卡片

P2 ─ 优化项，不阻塞
    ├── 删 3 张死表
    ├── v.cases 计数修复
    └── 案例卡片点击查看详情
```
