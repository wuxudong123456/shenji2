import fs from 'node:fs/promises';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const root='testdata/government_procurement_full_case';
const facts=JSON.parse(await fs.readFile('scripts/case_pack/case_facts.json','utf8'));
async function saveBook(path, sheets){
 const wb=Workbook.create();
 for(const [name,data] of sheets){const s=wb.worksheets.add(name);s.getRangeByIndexes(0,0,data.length,data[0].length).values=data;s.getRangeByIndexes(0,0,1,data[0].length).format={fill:'#1F4D78',font:{bold:true,color:'#FFFFFF'},wrapText:true};s.getUsedRange().format.borders={preset:'all',style:'thin',color:'#D9E0E8'};s.getUsedRange().format.autofitColumns();s.getUsedRange().format.autofitRows();s.freezePanes.freezeRows(1);}
 const out=await SpreadsheetFile.exportXlsx(wb);await out.save(path);
 for(const [name] of sheets){const png=await wb.render({sheetName:name,autoCrop:'all',scale:1,format:'png'});await fs.mkdir('tmp/case_pack_previews',{recursive:true});await fs.writeFile(`tmp/case_pack_previews/${path.split('/').pop()}-${name}.png`,new Uint8Array(await png.arrayBuffer()));}
}
const contracts=[['project_id','document_trace_id','template_name','doc_name','doc_type','party_a','party_b','amount','currency','sign_date','contract_no','procurement_method'],...facts.contracts.map(x=>['CASE-GP-2025',`TRACE-${x[0]}-CONTRACT`,'audit/合同协议类/合同',`${x[1]}_采购合同.pdf`,'采购合同','东河县教育局（虚构）',x[2],x[3],'CNY',x[5],x[1],x[4]])];
const rec=[['业务组','合同金额','发票金额','付款金额','保证金','保证金比例','预期'],['A',1680000,1680000,1680000,0,'=E2/B2','正常'],['B汇总',1820000,1820000,1820000,0,'=E3/B3','GP-001'],['C',800000,800000,780000,100000,'=E4/B4','GP-002/003/004']];
await saveBook(`${root}/04_结构化基准/结构化提取金标准.xlsx`,[['data_contracts',contracts],['字段勾稽',rec],['预期疑点',[['编码','名称','事实','等级'],...facts.findings]]]);
const acceptance=[['环节','检查项','预期','实际','结果'],['项目','创建项目','返回success=true','','待执行'],['上传/OCR','PDF上传','生成trace_id','','待执行'],['分类提取','合同模板','audit/合同协议类/合同','','待执行'],['规则','核心疑点','命中4个','','待执行'],['溯源','文件与页码','全部可定位','','待执行'],['文书','疑点报告','可生成并打开','','待执行']];
await saveBook(`${root}/09_验收记录/全流程验收记录.xlsx`,[['验收记录',acceptance],['规则验收',[['规则','预期命中'],...facts.findings.map(x=>[x[0],1])]]]);
console.log('WORKBOOKS_OK');
