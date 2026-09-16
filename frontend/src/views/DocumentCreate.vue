<template>
  <div class="doc-create-page">
    <el-card class="form-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span class="title">新建财务申报单据</span>
          <!-- 快捷演示样例填充 -->
          <div class="preset-buttons">
            <span class="preset-label">⚡ 快速载入演示场景：</span>
            <el-button size="small" type="primary" plain @click="applyPreset('REFLECT_DISAMBIGUATION')">
              ⚡ 差旅津贴免票消歧单 (申报1200=发票1000+津贴200)
            </el-button>
            <el-button size="small" type="warning" plain @click="applyPreset('OVER_BUDGET')">
              差旅住宿超标单
            </el-button>
            <el-button size="small" type="danger" plain @click="applyPreset('SEQUENTIAL_INVOICES')">
              连号发票异常单
            </el-button>
            <el-button size="small" type="danger" plain @click="applyPreset('DISHONEST_SUPPLIER')">
              失信供应商付款单
            </el-button>
            <el-button size="small" type="danger" plain @click="applyPreset('AMOUNT_MISMATCH')">
              发票金额不符单 (缺发票650元)
            </el-button>
            <el-button size="small" type="success" plain @click="applyPreset('AUTO_PASS')">
              小额免审直通单 (≤500元)
            </el-button>
          </div>

        </div>
      </template>

      <el-form :model="form" label-width="120px" class="create-form" @submit.prevent>
        <el-row :gutter="20">
          <el-col :span="8">
            <el-form-item label="单据类型" required>
              <el-select v-model="form.document_type" placeholder="请选择单据类型" style="width: 100%">
                <el-option label="差旅报销单 (TRAVEL_REIMBURSEMENT)" value="TRAVEL_REIMBURSEMENT" />
                <el-option label="费用报销单 (EXPENSE_REIMBURSEMENT)" value="EXPENSE_REIMBURSEMENT" />
                <el-option label="对公付款单 (CORP_PAYMENT)" value="CORP_PAYMENT" />
                <el-option label="预付款单 (ADVANCE_PAYMENT)" value="ADVANCE_PAYMENT" />
                <el-option label="批量付款单 (BATCH_PAYMENT)" value="BATCH_PAYMENT" />
              </el-select>
            </el-form-item>
          </el-col>

          <el-col :span="8">
            <el-form-item label="申报总金额" required>
              <el-input-number
                v-model="form.total_amount"
                :precision="2"
                :step="100"
                :min="0"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>

          <el-col :span="8">
            <el-form-item label="申报部门">
              <el-input v-model="form.department_name" placeholder="如：市场营销部" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="事由 / 标题" required>
          <el-input v-model="form.title" placeholder="如：2026年3月赴上海客户现场商务洽谈差旅报销" />
        </el-form-item>

        <!-- 票据上传与智能 OCR 识别专区 -->
        <div class="ocr-upload-section">
          <div class="section-title-row">
            <div class="ocr-title-left">
              <el-icon class="section-icon"><DocumentChecked /></el-icon>
              <span class="section-title">原始发票与附件凭证上传 (OCR 自动结构化解析)</span>
              <el-tag size="small" type="success" effect="plain">AI 识别后自动填充明细并完成平账</el-tag>
            </div>
            <div class="ocr-sample-presets">
              <span class="sample-hint">⚡ 示例票据快速识别：</span>
              <el-button-group size="small">
                <el-button type="success" plain @click="triggerOcrSample('HOTEL_1000')">🏨 住宿专票(1000元)</el-button>
                <el-button type="primary" plain @click="triggerOcrSample('HOTEL')">🏨 住宿专票(850元)</el-button>
                <el-button type="primary" plain @click="triggerOcrSample('TRAIN')">🚄 高铁票(650元)</el-button>
                <el-button type="warning" plain @click="triggerOcrSample('CORP_SERVICE')">💻 运维专票(5万元)</el-button>
                <el-button type="danger" plain @click="triggerOcrSample('SEQ_A')">📑 连号发票1(1200元)</el-button>
                <el-button type="danger" plain @click="triggerOcrSample('SEQ_B')">📑 连号发票2(1200元)</el-button>
              </el-button-group>
            </div>
          </div>

          <div class="ocr-content-grid">
            <!-- 本地文件上传拖拽区 -->
            <el-upload
              class="upload-box"
              drag
              action="#"
              :auto-upload="false"
              :show-file-list="false"
              :on-change="handleFileChange"
              accept=".pdf,.png,.jpg,.jpeg"
            >
              <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
              <div class="el-upload__text">
                拖拽发票图片 / PDF 至此，或 <em>点击本地上传</em>
              </div>
              <template #tip>
                <div class="el-upload__tip">
                  支持增值税电子专票/普票、行程单、PDF原件 (≤10MB)
                </div>
              </template>
            </el-upload>

            <!-- 已解析票据列表卡片 -->
            <div class="recognized-invoices-list" v-loading="ocrLoading" element-loading-text="AI 多模态 OCR 结构化解析中...">
              <div v-if="form.invoices && form.invoices.length" class="invoices-grid">
                <div v-for="(inv, idx) in form.invoices" :key="idx" class="inv-card">
                  <div class="inv-card-header">
                    <span class="inv-type-badge">{{ inv.invoice_type }}</span>
                    <el-button type="danger" link icon="Delete" @click="removeInvoice(idx)" />
                  </div>
                  <div class="inv-meta-row">
                    <span class="label">发票代码/号码：</span>
                    <span class="val font-mono">{{ inv.invoice_code !== 'NONE' ? inv.invoice_code + ' / ' : '' }}{{ inv.invoice_number }}</span>
                  </div>
                  <div class="inv-meta-row">
                    <span class="label">销方名称：</span>
                    <span class="val text-ellipsis" :title="inv.seller_name">{{ inv.seller_name }}</span>
                  </div>
                  <div class="inv-card-footer">
                    <span class="amount-label">价税合计：</span>
                    <strong class="amount-val">¥{{ Number(inv.total_amount).toFixed(2) }}</strong>
                    <el-tag size="small" type="success" style="margin-left: auto;">
                      置信度 {{ Math.round((inv.ocr_confidence || 0.985) * 100) }}%
                    </el-tag>
                  </div>
                </div>
              </div>
              <div v-else class="empty-ocr-box">
                <el-icon class="empty-icon"><PictureFilled /></el-icon>
                <span>暂未上传发票凭证，可通过左侧上传文件或右上角点击示例票据进行一键测试</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 明细项列表 -->

        <div class="line-items-section">
          <div class="section-title-row">
            <span class="section-title">申报费用明细项 (前置算术硬校验要求：明细之和必须平账)</span>
            <div class="section-actions">
              <span class="calc-sum-badge">
                明细合计：<strong>¥{{ lineItemsSum.toFixed(2) }}</strong>
                <el-tag
                  :type="isBalanced ? 'success' : 'danger'"
                  size="small"
                  style="margin-left: 8px;"
                >
                  {{ isBalanced ? '✓ 金额已平账' : `✗ 差额 ¥${Math.abs(form.total_amount - lineItemsSum).toFixed(2)}` }}
                </el-tag>
              </span>
              <el-button size="small" type="primary" plain icon="Plus" @click="addLineItem">
                添加明细行
              </el-button>
            </div>
          </div>

          <el-table :data="form.line_items" border stripe class="items-table">
            <el-table-column type="index" label="序号" width="60" align="center" />
            
            <el-table-column label="费用类型" width="180">
              <template #default="{ row }">
                <el-select
                  v-model="row.expense_type"
                  placeholder="选择或输入"
                  size="small"
                  filterable
                  allow-create
                  default-first-option
                >
                  <el-option-group label="差旅与外勤标准包干 (支持免票消歧)">
                    <el-option label="差旅津贴" value="差旅津贴" />
                    <el-option label="交通补贴" value="交通补贴" />
                    <el-option label="伙食补贴" value="伙食补贴" />
                    <el-option label="通讯补贴" value="通讯补贴" />
                    <el-option label="市内交通补贴" value="市内交通补贴" />
                    <el-option label="外勤包干" value="外勤包干" />
                  </el-option-group>
                  <el-option-group label="常规业务申报科目 (须附发票)">
                    <el-option label="住宿费" value="住宿费" />
                    <el-option label="交通费" value="交通费" />
                    <el-option label="餐饮费" value="餐饮费" />
                    <el-option label="办公用品" value="办公用品" />
                    <el-option label="技术服务费" value="技术服务费" />
                  </el-option-group>
                </el-select>
              </template>
            </el-table-column>

            <el-table-column label="明细内容描述" min-width="180">
              <template #default="{ row }">
                <el-input v-model="row.item_desc" placeholder="费用明细具体内容" size="small" />
              </template>
            </el-table-column>

            <el-table-column label="金额 (元)" width="160">
              <template #default="{ row }">
                <el-input-number v-model="row.amount" :precision="2" :min="0" size="small" style="width: 100%" />
              </template>
            </el-table-column>

            <el-table-column label="差旅城市" width="130">
              <template #default="{ row }">
                <el-input v-model="row.city_name" placeholder="如：北京/上海" size="small" />
              </template>
            </el-table-column>

            <el-table-column label="操作" width="80" align="center">
              <template #default="{ $index }">
                <el-button type="danger" link size="small" @click="removeLineItem($index)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div class="submit-actions">
          <el-button :disabled="submitting" @click="$router.push('/documents')">取消</el-button>
          <el-button type="primary" :loading="submitting" :disabled="submitting" @click="handleCreateAndSubmit">
            保存并立即提交审查
          </el-button>
        </div>
      </el-form>
    </el-card>

    <!-- 审查动态抽屉 -->
    <AuditStreamDrawer
      v-model="drawerVisible"
      :task-id="activeTaskId"
      :document-id="activeDocId"
      @completed="handleCompleted"
    />
  </div>
</template>

<script setup>
import { reactive, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElNotification } from 'element-plus'
import api from '@/api'
import AuditStreamDrawer from '@/components/AuditStreamDrawer.vue'

const router = useRouter()

const form = reactive({
  document_type: 'TRAVEL_REIMBURSEMENT',
  title: '上海研发技术协同与客户拜访差旅报销',
  department_name: '研发部',
  total_amount: 1450.00,
  currency: 'CNY',
  line_items: [
    { line_no: 1, expense_type: '交通费', item_desc: '京沪高铁二等座往返', amount: 650.00, city_name: '上海' },
    { line_no: 2, expense_type: '住宿费', item_desc: '汉庭商务酒店2晚', amount: 800.00, city_name: '上海' }
  ],
  invoices: []
})

const submitting = ref(false)
const idempotencyKey = ref('')
const drawerVisible = ref(false)
const activeTaskId = ref('')
const activeDocId = ref(null)
const ocrLoading = ref(false)

const lineItemsSum = computed(() => {
  return form.line_items.reduce((acc, cur) => acc + (Number(cur.amount) || 0), 0)
})

const isBalanced = computed(() => {
  return Math.abs(form.total_amount - lineItemsSum.value) < 0.01
})

const addLineItem = () => {
  form.line_items.push({
    line_no: form.line_items.length + 1,
    expense_type: '住宿费',
    item_desc: '',
    amount: 0,
    city_name: ''
  })
}

const removeLineItem = (idx) => {
  form.line_items.splice(idx, 1)
  form.line_items.forEach((item, i) => { item.line_no = i + 1 })
}

// 处理 OCR 结构化解析结果并自动平账填表明细
const processOcrResult = (res) => {
  const invData = res.invoice_data
  const recItem = res.recommended_line_item
  const fileInfo = res.file_info

  if (fileInfo && fileInfo.file_path) {
    invData.file_path = fileInfo.file_path
    invData.file_name = fileInfo.file_name
    invData.file_type = fileInfo.file_type
  }

  // 1. 存入已识别发票列表
  form.invoices.push(invData)

  // 2. 自动生成费用申报明细行 (若初始只有空行则替换，否则追加)
  if (recItem) {
    if (form.line_items.length === 1 && Number(form.line_items[0].amount) === 0 && !form.line_items[0].item_desc) {
      form.line_items = []
    }
    form.line_items.push({
      line_no: form.line_items.length + 1,
      expense_type: recItem.expense_type,
      item_desc: recItem.item_desc,
      amount: recItem.amount,
      city_name: recItem.city_name || ''
    })
  }

  // 3. 自动同步申报总额完成前置硬校验平账
  const sum = form.line_items.reduce((acc, cur) => acc + (Number(cur.amount) || 0), 0)
  form.total_amount = Number(sum.toFixed(2))

  ElNotification({
    title: '✅ 发票 OCR 识别就绪',
    message: `已解析发票 [${invData.invoice_number}]，自动生成【${recItem.expense_type}】明细 ¥${invData.total_amount} 并自动平账！`,
    type: 'success',
    duration: 3500
  })
}

// 示例发票快速识别触发
const triggerOcrSample = async (sampleType) => {
  ocrLoading.value = true
  try {
    const formData = new FormData()
    formData.append('sample_type', sampleType)
    const res = await api.post('/documents/upload-invoice', formData)
    processOcrResult(res)
  } catch (err) {
    ElMessage.error('OCR 提取失败: ' + (err.message || '网络连接异常'))
  } finally {
    ocrLoading.value = false
  }
}

// 本地真实图片/PDF上传并触发后端 OCR
const handleFileChange = async (uploadFile) => {
  if (!uploadFile.raw) return
  ocrLoading.value = true
  try {
    const formData = new FormData()
    formData.append('file', uploadFile.raw)
    const res = await api.post('/documents/upload-invoice', formData)
    processOcrResult(res)
  } catch (err) {
    ElMessage.error('发票原件解析失败: ' + (err.message || '网络连接异常'))
  } finally {
    ocrLoading.value = false
  }
}

const removeInvoice = (idx) => {
  form.invoices.splice(idx, 1)
  ElMessage.info('已移除发票凭证')
}

// 快速应用典型场景
const applyPreset = (type) => {
  form.invoices = []
  if (type === 'REFLECT_DISAMBIGUATION') {
    // 二阶反思消歧场景：申报 1200 元 = 住宿发票 1000 元 + 免票差旅定额津贴 200 元
    form.document_type = 'TRAVEL_REIMBURSEMENT'
    form.title = '上海研发技术交流差旅报销 (触发 Reviewer 二阶反思消歧免票放行)'
    form.department_name = '研发部'
    form.total_amount = 1200.00
    form.line_items = [
      { line_no: 1, expense_type: '住宿费', item_desc: '汉庭商务酒店2晚', amount: 1000.00, city_name: '上海' },
      { line_no: 2, expense_type: '差旅津贴', item_desc: '出差2天定额包干津贴(免发票)', amount: 200.00, city_name: '上海' }
    ]
    form.invoices = [
      {
        invoice_code: '031002000888',
        invoice_number: '88203998',
        invoice_type: '增值税专用发票',
        total_amount: 1000.00,
        untaxed_amount: 943.40,
        tax_amount: 56.60,
        tax_rate: 0.06,
        seller_name: '汉庭星空(上海)酒店管理有限公司',
        seller_tax_id: '91310101746182937X',
        buyer_name: '北京智能前沿科技有限公司',
        buyer_tax_id: '91110108MA01XXXXXX',
        issue_date: '2026-03-12',
        invoice_hash: 'sha256_preset_hotel_1000',
        ocr_confidence: 0.99
      }
    ]
  } else if (type === 'OVER_BUDGET') {
    // 差旅住宿超标 (上海限额 500元/天，填入 850元/天)
    form.document_type = 'TRAVEL_REIMBURSEMENT'
    form.title = '上海商务出差超标报销 (触发 R05 超标风控)'
    form.department_name = '市场部'
    form.total_amount = 1500.00
    form.line_items = [
      { line_no: 1, expense_type: '交通费', item_desc: '高铁二等座', amount: 650.00, city_name: '上海' },
      { line_no: 2, expense_type: '住宿费', item_desc: '豪华五星酒店1晚', amount: 850.00, city_name: '上海' }
    ]
    form.invoices = [
      {
        invoice_code: '031002000888', invoice_number: '88203991', invoice_type: '增值税专用发票',
        total_amount: 850.00, untaxed_amount: 801.89, tax_amount: 48.11, tax_rate: 0.06,
        seller_name: '上海和平饭店管理有限公司', seller_tax_id: '91310101746182937X',
        buyer_name: '北京智能前沿科技有限公司', buyer_tax_id: '91110108MA01XXXXXX',
        issue_date: '2026-03-12', invoice_hash: 'sha256_preset_hotel_850', ocr_confidence: 0.99
      },
      {
        invoice_code: 'NONE', invoice_number: 'G1029381', invoice_type: '铁路电子客票',
        total_amount: 650.00, untaxed_amount: 596.33, tax_amount: 53.67, tax_rate: 0.09,
        seller_name: '中国铁路网络有限公司', seller_tax_id: '91110000717882931T',
        buyer_name: '北京智能前沿科技有限公司', buyer_tax_id: '91110108MA01XXXXXX',
        issue_date: '2026-03-11', invoice_hash: 'sha256_preset_train_650', ocr_confidence: 0.98
      }
    ]
  } else if (type === 'SEQUENTIAL_INVOICES') {
    // 连号发票嫌疑
    form.document_type = 'EXPENSE_REIMBURSEMENT'
    form.title = '部门团建与办公物资集中采购 (触发 R10 连号发票风控)'
    form.department_name = '行政部'
    form.total_amount = 2400.00
    form.line_items = [
      { line_no: 1, expense_type: '餐饮费', item_desc: '团建聚餐发票1', amount: 1200.00, city_name: '北京' },
      { line_no: 2, expense_type: '餐饮费', item_desc: '团建聚餐发票2', amount: 1200.00, city_name: '北京' }
    ]
    form.invoices = [
      {
        invoice_code: '011002000222', invoice_number: '88203001', invoice_type: '增值税电子普通发票',
        total_amount: 1200.00, untaxed_amount: 1132.08, tax_amount: 67.92, tax_rate: 0.06,
        seller_name: '北京餐饮服务中心', seller_tax_id: '91110108551385082Q',
        buyer_name: '北京智能前沿科技有限公司', buyer_tax_id: '91110108MA01XXXXXX',
        issue_date: '2026-03-10', invoice_hash: 'sha256_preset_seq_01', ocr_confidence: 0.98
      },
      {
        invoice_code: '011002000222', invoice_number: '88203002', invoice_type: '增值税电子普通发票',
        total_amount: 1200.00, untaxed_amount: 1132.08, tax_amount: 67.92, tax_rate: 0.06,
        seller_name: '北京餐饮服务中心', seller_tax_id: '91110108551385082Q',
        buyer_name: '北京智能前沿科技有限公司', buyer_tax_id: '91110108MA01XXXXXX',
        issue_date: '2026-03-10', invoice_hash: 'sha256_preset_seq_02', ocr_confidence: 0.98
      }
    ]
  } else if (type === 'DISHONEST_SUPPLIER') {
    // 对公付款：黑名单/失信被执行人供应商
    form.document_type = 'CORP_PAYMENT'
    form.title = '对外技术运维服务采购款 (触发 R12/R15 供应商黑名单风控)'
    form.department_name = '运维部'
    form.total_amount = 50000.00
    form.line_items = [
      { line_no: 1, expense_type: '技术服务费', item_desc: '云架构运维第一期付款', amount: 50000.00, city_name: '北京' }
    ]
    form.invoices = [
      {
        invoice_code: '011002000999', invoice_number: '99823019', invoice_type: '全电增值税专用发票',
        total_amount: 50000.00, untaxed_amount: 47169.81, tax_amount: 2830.19, tax_rate: 0.06,
        seller_name: '北京神州数码技术有限公司', seller_tax_id: '91110108551385082Q',
        buyer_name: '北京智能前沿科技有限公司', buyer_tax_id: '91110108MA01XXXXXX',
        issue_date: '2026-03-08', invoice_hash: 'sha256_preset_corp_50k', ocr_confidence: 0.99
      }
    ]
  } else if (type === 'AMOUNT_MISMATCH') {
    // 申报金额与发票不平 (触发 R02 发票价税合计与申报总额不符，缺发票 650 元)
    form.document_type = 'TRAVEL_REIMBURSEMENT'
    form.title = '上海商务考察差旅报销 (触发 R02 发票总额与单据申报不平风控)'
    form.department_name = '研发部'
    form.total_amount = 1500.00
    form.line_items = [
      { line_no: 1, expense_type: '交通费', item_desc: '高铁二等座往返', amount: 650.00, city_name: '上海' },
      { line_no: 2, expense_type: '住宿费', item_desc: '汉庭酒店2晚', amount: 850.00, city_name: '上海' }
    ]
    // 仅上传了 850 元的发票，缺少 650 元的高铁发票，触发发票与申报总额不符
    form.invoices = [
      {
        invoice_code: '031002000888', invoice_number: '88203991', invoice_type: '增值税专用发票',
        total_amount: 850.00, untaxed_amount: 801.89, tax_amount: 48.11, tax_rate: 0.06,
        seller_name: '汉庭星空（上海）酒店管理有限公司', seller_tax_id: '91310101746182937X',
        buyer_name: '北京智能前沿科技有限公司', buyer_tax_id: '91110108MA01XXXXXX',
        issue_date: '2026-03-12', invoice_hash: 'sha256_preset_hotel_850_only', ocr_confidence: 0.99
      }
    ]
  } else if (type === 'AUTO_PASS') {
    // 小额免审直通
    form.document_type = 'EXPENSE_REIMBURSEMENT'
    form.title = '市内交通打车费 (触发 RULE_AUTO_PASS 小额免审直通)'
    form.department_name = '综合部'
    form.total_amount = 320.00
    form.line_items = [
      { line_no: 1, expense_type: '交通费', item_desc: '商务出行滴滴打车', amount: 320.00, city_name: '北京' }
    ]
  }

  ElMessage.info('已载入测试场景与对应票据，可直接点击提交审查！')
}


const handleCreateAndSubmit = async () => {
  if (!isBalanced.value) {
    ElMessage.error(`算术平账硬校验失败：单据总额 ¥${form.total_amount} 与明细和 ¥${lineItemsSum.value} 不平！`)
    return
  }

  if (submitting.value) return
  submitting.value = true
  try {
    // 1. 创建单据草稿 (附带幂等防重键)
    if (!idempotencyKey.value) {
      idempotencyKey.value = `create_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
    }
    const payload = {
      ...form,
      idempotency_key: idempotencyKey.value
    }
    const created = await api.post('/documents', payload)
    ElMessage.success('单据保存成功，正在启动多智能体审查...')

    // 2. 提交审查
    const subRes = await api.post(`/documents/${created.id}/submit`)
    activeDocId.value = created.id
    activeTaskId.value = subRes.task_id
    console.log(`[DocumentCreate] 提交成功: document_id=${created.id}, audit_version=${subRes.audit_version}, task_id=${subRes.task_id}, reused=${subRes.reused}`)
    drawerVisible.value = true
  } catch (err) {
    console.error('[DocumentCreate] 提交失败:', err)
    ElMessage.error(err.response?.data?.detail || err.message || '提交审查失败')
  } finally {
    submitting.value = false
  }
}

const handleCompleted = () => {
  if (activeTaskId.value) {
    router.push({
      path: `/audits/${activeDocId.value}`,
      query: { task_id: activeTaskId.value }
    })
  } else {
    router.push(`/audits/${activeDocId.value}`)
  }
}
</script>

<style scoped>
.doc-create-page {
  max-width: 1100px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.card-header .title {
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
}

.preset-buttons {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.preset-label {
  font-size: 12px;
  color: #64748b;
}

.line-items-section {
  margin-top: 24px;
  padding: 16px;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
}

.section-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}

.calc-sum-badge {
  font-size: 13px;
  color: #475569;
  margin-right: 12px;
}

.items-table {
  background: #ffffff;
}

.ocr-upload-section {
  margin-top: 20px;
  padding: 16px;
  background: #f0fdf4;
  border-radius: 8px;
  border: 1px solid #bbf7d0;
}

.ocr-title-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-icon {
  font-size: 16px;
  color: #16a34a;
}

.ocr-sample-presets {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.sample-hint {
  font-size: 12px;
  color: #4b5563;
}

.ocr-content-grid {
  display: grid;
  grid-template-columns: minmax(280px, 340px) 1fr;
  gap: 16px;
  margin-top: 12px;
}

.upload-box :deep(.el-upload-dragger) {
  padding: 20px 10px;
  background: #ffffff;
  border: 1px dashed #86efac;
  border-radius: 8px;
}

.recognized-invoices-list {
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  padding: 12px;
  min-height: 140px;
}

.invoices-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.inv-card {
  padding: 12px;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  background: #fafafa;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.inv-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.inv-type-badge {
  font-size: 12px;
  font-weight: 600;
  color: #0284c7;
  background: #e0f2fe;
  padding: 2px 6px;
  border-radius: 4px;
}

.inv-meta-row {
  display: flex;
  font-size: 12px;
  color: #475569;
}

.inv-meta-row .label {
  color: #94a3b8;
  min-width: 80px;
}

.inv-meta-row .val {
  color: #1e293b;
  font-weight: 500;
}

.inv-card-footer {
  display: flex;
  align-items: center;
  margin-top: 4px;
  padding-top: 6px;
  border-top: 1px dashed #e2e8f0;
}

.amount-label {
  font-size: 12px;
  color: #64748b;
}

.amount-val {
  font-size: 15px;
  color: #e11d48;
  margin-left: 4px;
}

.empty-ocr-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 120px;
  color: #9ca3af;
  font-size: 13px;
  gap: 8px;
}

.empty-icon {
  font-size: 28px;
  color: #cbd5e1;
}

.text-ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.submit-actions {
  margin-top: 28px;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>

