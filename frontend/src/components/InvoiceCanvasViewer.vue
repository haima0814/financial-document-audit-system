<template>
  <div class="invoice-canvas-container">
    <!-- 顶部控制栏 -->
    <div class="canvas-header">
      <div class="header-left">
        <div class="header-title">
          <el-icon><Document /></el-icon>
          <span>票据原件与 OCR 视觉证据锚点</span>
        </div>
        <el-tag v-if="hasRealImage && viewMode === 'original'" size="small" type="success" effect="light">
          📸 原件真图模式
        </el-tag>
        <el-tag v-else size="small" type="info" effect="light">
          📄 结构化数字票面
        </el-tag>
      </div>

      <div class="header-controls">
        <!-- 视图切换 (当有原图时可自由切换原图与结构化视图) -->
        <el-radio-group v-if="hasRealImage" v-model="viewMode" size="small" style="margin-right: 8px;">
          <el-radio-button value="original">
            <el-icon style="margin-right: 4px;"><Picture /></el-icon>发票原图
          </el-radio-button>
          <el-radio-button value="digital">
            <el-icon style="margin-right: 4px;"><Document /></el-icon>数字票面
          </el-radio-button>
        </el-radio-group>

        <!-- 锚点开关 -->
        <el-button
          size="small"
          :type="showAnchors ? 'primary' : 'default'"
          @click="showAnchors = !showAnchors"
          plain
          style="margin-right: 8px;"
        >
          <el-icon><Aim /></el-icon>
          <span style="margin-left: 4px;">{{ showAnchors ? '隐藏锚点' : '显示锚点' }}</span>
        </el-button>

        <!-- 缩放控制 -->
        <el-button-group size="small">
          <el-button :icon="ZoomOut" @click="zoomOut" :disabled="zoom <= 0.6" />
          <el-button @click="resetZoom">{{ Math.round(zoom * 100) }}%</el-button>
          <el-button :icon="ZoomIn" @click="zoomIn" :disabled="zoom >= 2.0" />
        </el-button-group>
      </div>
    </div>

    <!-- 快捷字段高亮滤镜栏 -->
    <div class="field-filter-bar" v-if="showAnchors && renderedBoxes.length > 0">
      <span class="filter-label">视觉锚点筛选：</span>
      <el-tag
        size="small"
        :effect="activeFilter === 'all' ? 'dark' : 'plain'"
        class="filter-tag"
        @click="activeFilter = 'all'"
      >
        全部锚点 ({{ allBoxes.length }})
      </el-tag>
      <el-tag
        v-if="selectedFindingAnchor"
        size="small"
        type="danger"
        :effect="activeFilter === 'finding' ? 'dark' : 'plain'"
        class="filter-tag pulse-tag"
        @click="activeFilter = 'finding'"
      >
        ⚠️ 风险证据: {{ selectedFinding?.rule_name || '异常项' }}
      </el-tag>
      <el-tag
        v-if="rawBBoxes.total_amount"
        size="small"
        type="warning"
        :effect="activeFilter === 'total_amount' ? 'dark' : 'plain'"
        class="filter-tag"
        @click="activeFilter = 'total_amount'"
      >
        💰 价税合计 ¥{{ invoiceData.total_amount || '-' }}
      </el-tag>
      <el-tag
        v-if="rawBBoxes.invoice_number"
        size="small"
        type="primary"
        :effect="activeFilter === 'invoice_number' ? 'dark' : 'plain'"
        class="filter-tag"
        @click="activeFilter = 'invoice_number'"
      >
        🔢 发票号码 {{ invoiceData.invoice_number || '-' }}
      </el-tag>
      <el-tag
        v-if="rawBBoxes.seller_info"
        size="small"
        type="success"
        :effect="activeFilter === 'seller_info' ? 'dark' : 'plain'"
        class="filter-tag"
        @click="activeFilter = 'seller_info'"
      >
        🏢 销售方信息
      </el-tag>
    </div>

    <!-- 票据展示与动态 BBox 叠加层视口 -->
    <div class="canvas-viewport" ref="viewportRef">
      <div
        class="invoice-canvas-sheet"
        :style="{ transform: `scale(${zoom})`, transformOrigin: 'top center' }"
      >
        <!-- 模式一：真实发票原图展示 -->
        <div v-if="viewMode === 'original' && hasRealImage" class="real-image-container">
          <img
            ref="invoiceImgRef"
            :src="invoiceData.file_path"
            class="invoice-real-image"
            alt="票据原件照片"
            @load="onImageLoad"
            @error="onImageError"
          />

          <!-- SVG 视觉 BBox 锚点高亮层 (贴合真实原图 1000x1000 归一化网格) -->
          <svg
            v-if="showAnchors"
            class="bbox-overlay"
            viewBox="0 0 1000 1000"
            preserveAspectRatio="none"
          >
            <g
              v-for="(box, idx) in renderedBoxes"
              :key="idx"
              class="bbox-group"
              :class="{ 'is-focused': box.id === activeFilter || activeFilter === 'all' }"
            >
              <!-- 矩形边框高亮 -->
              <rect
                :x="box.box[1]"
                :y="box.box[0]"
                :width="Math.max(20, box.box[3] - box.box[1])"
                :height="Math.max(16, box.box[2] - box.box[0])"
                :class="['anchor-rect', box.riskLevel || 'normal', { pulse: box.highlighted }]"
              />
              <!-- 悬浮标签背景与文字 -->
              <g class="anchor-label-group">
                <rect
                  :x="box.box[1]"
                  :y="box.box[0] > 26 ? box.box[0] - 22 : box.box[2] + 4"
                  :width="Math.max(60, box.label.length * 11 + 16)"
                  height="20"
                  :class="['label-bg', box.riskLevel || 'normal']"
                  rx="3"
                />
                <text
                  :x="box.box[1] + 8"
                  :y="box.box[0] > 26 ? box.box[0] - 8 : box.box[2] + 18"
                  class="anchor-label-text"
                >
                  {{ box.label }}
                </text>
              </g>
            </g>
          </svg>
        </div>

        <!-- 模式二：仿真结构化发票外观 -->
        <div v-else class="mock-invoice-paper">
          <div class="inv-stamp">发票监制章</div>
          <div class="inv-header">
            <div class="inv-title">增值税电子普通发票</div>
            <div class="inv-meta">
              <div>发票代码：<span>{{ invoiceData.invoice_code || '011002000111' }}</span></div>
              <div>发票号码：<span>{{ invoiceData.invoice_number || '23456789' }}</span></div>
              <div>开票日期：<span>{{ invoiceData.issue_date || '2026-03-12' }}</span></div>
            </div>
          </div>

          <div class="inv-body">
            <div class="inv-row">
              <div class="inv-col buyer">
                <div class="col-title">购买方名称：{{ invoiceData.buyer_name || '北京智能前沿科技有限公司' }}</div>
                <div>统一社会信用代码：{{ invoiceData.buyer_tax_id || '91110108MA01XXXXXX' }}</div>
              </div>
              <div class="inv-col seller">
                <div class="col-title">销售方名称：{{ invoiceData.seller_name || '北京神州数码技术有限公司' }}</div>
                <div>统一社会信用代码：{{ invoiceData.seller_tax_id || '91110108551385082Q' }}</div>
              </div>
            </div>

            <table class="inv-table">
              <thead>
                <tr>
                  <th>货物或应税劳务、服务名称</th>
                  <th>数量</th>
                  <th>单价</th>
                  <th>金额</th>
                  <th>税率</th>
                  <th>税额</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{{ invoiceData.item_name || '企业差旅服务费 / 办公用品' }}</td>
                  <td>1</td>
                  <td>¥{{ invoiceData.untaxed_amount || invoiceData.total_amount || '1,200.00' }}</td>
                  <td>¥{{ invoiceData.untaxed_amount || invoiceData.total_amount || '1,200.00' }}</td>
                  <td>6%</td>
                  <td>¥{{ invoiceData.tax_amount || '72.00' }}</td>
                </tr>
              </tbody>
            </table>

            <div class="inv-total-row">
              <div>价税合计（大写）：<span>{{ invoiceData.total_amount ? '壹仟贰佰元整' : '壹仟贰佰元整' }}</span></div>
              <div>（小写）：<strong>¥{{ invoiceData.total_amount || '1,200.00' }}</strong></div>
            </div>
          </div>

          <!-- 仿真票面上的 SVG 叠加层 -->
          <svg
            v-if="showAnchors"
            class="bbox-overlay"
            viewBox="0 0 1000 1000"
            preserveAspectRatio="none"
          >
            <g
              v-for="(box, idx) in renderedBoxes"
              :key="idx"
              class="bbox-group"
              :class="{ 'is-focused': box.id === activeFilter || activeFilter === 'all' }"
            >
              <rect
                :x="box.box[1]"
                :y="box.box[0]"
                :width="Math.max(20, box.box[3] - box.box[1])"
                :height="Math.max(16, box.box[2] - box.box[0])"
                :class="['anchor-rect', box.riskLevel || 'normal', { pulse: box.highlighted }]"
              />
              <g class="anchor-label-group">
                <rect
                  :x="box.box[1]"
                  :y="box.box[0] > 26 ? box.box[0] - 22 : box.box[2] + 4"
                  :width="Math.max(60, box.label.length * 11 + 16)"
                  height="20"
                  :class="['label-bg', box.riskLevel || 'normal']"
                  rx="3"
                />
                <text
                  :x="box.box[1] + 8"
                  :y="box.box[0] > 26 ? box.box[0] - 8 : box.box[2] + 18"
                  class="anchor-label-text"
                >
                  {{ box.label }}
                </text>
              </g>
            </g>
          </svg>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { Document, Picture, ZoomIn, ZoomOut, Aim } from '@element-plus/icons-vue'

const props = defineProps({
  invoiceData: {
    type: Object,
    default: () => ({})
  },
  selectedFinding: {
    type: Object,
    default: null
  }
})

const zoom = ref(1)
const showAnchors = ref(true)
const activeFilter = ref('all')
const imageLoadError = ref(false)

// 判断是否有有效的发票原图 (支持 jpg, jpeg, png, webp, bmp)
const hasRealImage = computed(() => {
  const path = props.invoiceData?.file_path
  if (!path || imageLoadError.value) return false
  const lower = path.toLowerCase()
  return (
    lower.endsWith('.png') ||
    lower.endsWith('.jpg') ||
    lower.endsWith('.jpeg') ||
    lower.endsWith('.webp') ||
    lower.endsWith('.bmp')
  )
})

const viewMode = ref('original')

// 当 invoiceData.file_path 变化时，如果存在真实图片则默认切换为 original
watch(
  () => props.invoiceData?.file_path,
  (newPath) => {
    imageLoadError.value = false
    if (newPath && (newPath.toLowerCase().endsWith('.jpg') || newPath.toLowerCase().endsWith('.png') || newPath.toLowerCase().endsWith('.jpeg'))) {
      viewMode.value = 'original'
    } else {
      viewMode.value = 'digital'
    }
  },
  { immediate: true }
)

// 监听选中的风险项，自动将过滤器置为 finding
watch(
  () => props.selectedFinding,
  (newFinding) => {
    if (newFinding && newFinding.primary_visual_anchor?.box_2d) {
      activeFilter.value = 'finding'
    }
  }
)

const zoomIn = () => {
  if (zoom.value < 2.0) zoom.value = +(zoom.value + 0.15).toFixed(2)
}

const zoomOut = () => {
  if (zoom.value > 0.5) zoom.value = +(zoom.value - 0.15).toFixed(2)
}

const resetZoom = () => {
  zoom.value = 1
}

const onImageLoad = () => {
  imageLoadError.value = false
}

const onImageError = () => {
  console.warn('发票原图加载失败，回退至数字票面视图:', props.invoiceData?.file_path)
  imageLoadError.value = true
  viewMode.value = 'digital'
}

// 提取当前原始 BBox 字典
const rawBBoxes = computed(() => {
  return props.invoiceData?.bbox_positions || props.invoiceData?.raw_payload?.bbox_positions || {}
})

// 提取风险项中的视觉锚点
const selectedFindingAnchor = computed(() => {
  return props.selectedFinding?.primary_visual_anchor?.box_2d || null
})

// 动态合成视觉锚点列表
const allBoxes = computed(() => {
  const list = []

  // 1. 如果有选中的风险项，优先级最高
  if (props.selectedFinding && props.selectedFinding.primary_visual_anchor) {
    const anchor = props.selectedFinding.primary_visual_anchor
    if (anchor.box_2d) {
      list.push({
        id: 'finding',
        box: anchor.box_2d,
        label: `⚠️ ${props.selectedFinding.rule_name || '风险嫌疑字段'}`,
        riskLevel: 'risk',
        highlighted: true
      })
    }
  }

  // 2. 从 OCR 解析结果中提取关键业务字段锚点
  const bboxes = rawBBoxes.value
  if (bboxes.total_amount) {
    list.push({
      id: 'total_amount',
      box: bboxes.total_amount,
      label: `价税合计: ¥${props.invoiceData.total_amount || ''}`,
      riskLevel: 'amount',
      highlighted: activeFilter.value === 'total_amount'
    })
  }
  if (bboxes.invoice_number) {
    list.push({
      id: 'invoice_number',
      box: bboxes.invoice_number,
      label: `发票号码: ${props.invoiceData.invoice_number || ''}`,
      riskLevel: 'code',
      highlighted: activeFilter.value === 'invoice_number'
    })
  }
  if (bboxes.seller_info) {
    list.push({
      id: 'seller_info',
      box: bboxes.seller_info,
      label: `销售方信息`,
      riskLevel: 'seller',
      highlighted: activeFilter.value === 'seller_info'
    })
  }
  if (bboxes.issue_date) {
    list.push({
      id: 'issue_date',
      box: bboxes.issue_date,
      label: `开票日期: ${props.invoiceData.issue_date || ''}`,
      riskLevel: 'info',
      highlighted: activeFilter.value === 'issue_date'
    })
  }

  // 3. 如果没有从真实图片提取到 BBox 且为数字模板视图，提供兜底标准增值税锚点
  if (list.length === 0 && viewMode.value === 'digital') {
    list.push(
      { id: 'total_amount', box: [730, 680, 780, 950], label: '价税合计总额', riskLevel: 'amount', highlighted: false },
      { id: 'invoice_number', box: [125, 650, 155, 950], label: '发票号码 (No)', riskLevel: 'code', highlighted: false },
      { id: 'seller_info', box: [200, 510, 320, 950], label: '销售方信息 (USCC)', riskLevel: 'seller', highlighted: false }
    )
  }

  return list
})

// 根据当前选中的过滤器过滤显示的 BBox
const renderedBoxes = computed(() => {
  if (activeFilter.value === 'all') {
    return allBoxes.value
  }
  return allBoxes.value.filter(b => b.id === activeFilter.value || (activeFilter.value === 'finding' && b.id === 'finding'))
})
</script>

<style scoped>
.invoice-canvas-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #f8fafc;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #e2e8f0;
}

.canvas-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.header-controls {
  display: flex;
  align-items: center;
}

.field-filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: #f1f5f9;
  border-bottom: 1px solid #e2e8f0;
  flex-wrap: wrap;
}

.filter-label {
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
}

.filter-tag {
  cursor: pointer;
  user-select: none;
  transition: all 0.2s;
}

.filter-tag:hover {
  opacity: 0.85;
  transform: translateY(-1px);
}

.pulse-tag {
  animation: pulse-tag-border 1.5s infinite;
}

@keyframes pulse-tag-border {
  0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }
  70% { box-shadow: 0 0 0 6px rgba(239, 68, 68, 0); }
  100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
}

.canvas-viewport {
  flex: 1;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 24px;
  overflow: auto;
}

.invoice-canvas-sheet {
  position: relative;
  transition: transform 0.15s ease-out;
}

/* 真实发票原图容器 */
.real-image-container {
  position: relative;
  display: inline-block;
  max-width: 680px;
  background: #ffffff;
  border-radius: 6px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.12), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
  overflow: hidden;
  border: 1px solid #cbd5e1;
}

.invoice-real-image {
  display: block;
  width: 100%;
  max-width: 680px;
  height: auto;
  object-fit: contain;
}

/* 仿真发票外观 */
.mock-invoice-paper {
  position: relative;
  width: 640px;
  min-height: 480px;
  background: #ffffff;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
  border-radius: 6px;
  padding: 28px;
  font-size: 12px;
  color: #334155;
  border: 1px solid #cbd5e1;
}

.inv-stamp {
  position: absolute;
  top: 20px;
  right: 40px;
  width: 80px;
  height: 80px;
  border: 2px solid #ef4444;
  border-radius: 50%;
  color: #ef4444;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
  transform: rotate(-15deg);
  opacity: 0.75;
  pointer-events: none;
}

.inv-header {
  text-align: center;
  margin-bottom: 20px;
  border-bottom: 2px solid #94a3b8;
  padding-bottom: 12px;
}

.inv-title {
  font-size: 18px;
  font-weight: bold;
  letter-spacing: 2px;
  color: #0f172a;
}

.inv-meta {
  display: flex;
  justify-content: space-around;
  margin-top: 8px;
  font-size: 12px;
  color: #64748b;
}

.inv-row {
  display: flex;
  border: 1px solid #cbd5e1;
  margin-bottom: 12px;
}

.inv-col {
  flex: 1;
  padding: 8px;
  line-height: 1.6;
}

.inv-col:first-child {
  border-right: 1px solid #cbd5e1;
}

.col-title {
  font-weight: 600;
  color: #1e293b;
}

.inv-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 12px;
  text-align: center;
}

.inv-table th, .inv-table td {
  border: 1px solid #cbd5e1;
  padding: 8px 4px;
}

.inv-table th {
  background: #f1f5f9;
  font-weight: 600;
}

.inv-total-row {
  display: flex;
  justify-content: space-between;
  padding: 10px;
  border: 1px solid #cbd5e1;
  background: #f8fafc;
  font-size: 13px;
}

/* SVG BBox 绝对定位重叠在发票之上 */
.bbox-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.anchor-rect {
  fill: rgba(59, 130, 246, 0.15);
  stroke: #3b82f6;
  stroke-width: 2.5;
  rx: 3;
  transition: all 0.2s ease;
}

.anchor-rect.risk {
  fill: rgba(239, 68, 68, 0.25);
  stroke: #ef4444;
  stroke-width: 3.5;
}

.anchor-rect.amount {
  fill: rgba(245, 158, 11, 0.2);
  stroke: #f59e0b;
  stroke-width: 3;
}

.anchor-rect.code {
  fill: rgba(139, 92, 246, 0.2);
  stroke: #8b5cf6;
  stroke-width: 2.5;
}

.anchor-rect.seller {
  fill: rgba(16, 185, 129, 0.2);
  stroke: #10b981;
  stroke-width: 2.5;
}

.anchor-rect.pulse {
  animation: pulse-border 1.5s infinite;
}

@keyframes pulse-border {
  0% { stroke-width: 3; stroke-opacity: 1; }
  50% { stroke-width: 6; stroke-opacity: 0.6; }
  100% { stroke-width: 3; stroke-opacity: 1; }
}

.label-bg {
  fill: #3b82f6;
  opacity: 0.92;
}

.label-bg.risk {
  fill: #ef4444;
}

.label-bg.amount {
  fill: #f59e0b;
}

.label-bg.code {
  fill: #8b5cf6;
}

.label-bg.seller {
  fill: #10b981;
}

.anchor-label-text {
  fill: #ffffff;
  font-size: 11px;
  font-weight: bold;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
</style>
