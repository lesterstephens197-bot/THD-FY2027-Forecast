import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO

# ---------------------------------------------------------
# 1. 页面基础配置
# ---------------------------------------------------------
st.set_page_config(
    page_title="2027 HD 销量与销售额预估工作台",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    .main {background-color: #f8f9fa;}
    div[data-testid="metric-container"] {
        background-color: #ffffff;
        border: 1px solid #e6e9ef;
        padding: 15px 20px;
        border-radius: 8px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    </style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. 数据清洗与双渠道对齐引擎
# ---------------------------------------------------------
@st.cache_data
def load_and_normalize_data(vdf_file, df_file, exclude_cancelled=True):
    frames = []
    sku_meta_map = {}

    # --- 处理 VDF 数据 ---
    if vdf_file is not None:
        vdf = pd.read_excel(vdf_file) if vdf_file.name.endswith(('xlsx', 'xls')) else pd.read_csv(vdf_file)
        vdf.columns = vdf.columns.str.strip()
        
        if exclude_cancelled and 'Line Status' in vdf.columns:
            vdf = vdf[~vdf['Line Status'].astype(str).str.lower().isin(['cancelled', 'canceled', 'rejected'])]
        
        # 建立 SKU 元数据字典（供 DF 匹配品牌与属性）
        meta_cols = [c for c in ['产品SKU', '品牌', '产品属性', '产品名称', '产品定位'] if c in vdf.columns]
        if '产品SKU' in meta_cols:
            meta_df = vdf[meta_cols].dropna(subset=['产品SKU']).drop_duplicates(subset=['产品SKU'])
            sku_meta_map = meta_df.set_index('产品SKU').to_dict('index')

        vdf_norm = pd.DataFrame({
            'Channel': 'VDF',
            'Date': pd.to_datetime(vdf['Order Date'], errors='coerce'),
            'SKU': vdf['产品SKU'].astype(str).str.strip(),
            'Product_Name': vdf.get('产品名称', vdf.get('Description', '')),
            'Brand': vdf.get('品牌', 'Unknown').fillna('Unknown'),
            'Category': vdf.get('产品属性', '未分类').fillna('未分类'),
            'Positioning': vdf.get('产品定位', '常规款').fillna('常规款'),
            'Owner': vdf.get('运营', '未分配').fillna('未分配'),
            'Qty': pd.to_numeric(vdf['Quantity'], errors='coerce').fillna(0),
            'Sales': pd.to_numeric(vdf['Total Cost'], errors='coerce').fillna(0),
            'Unit_Price': pd.to_numeric(vdf['Unit Cost'], errors='coerce').fillna(0)
        })
        frames.append(vdf_norm)

    # --- 处理 DF 数据 ---
    if df_file is not None:
        df_raw = pd.read_excel(df_file) if df_file.name.endswith(('xlsx', 'xls')) else pd.read_csv(df_file)
        df_raw.columns = df_raw.columns.str.strip()
        
        skus = df_raw['产品SKU'].astype(str).str.strip()
        qty = pd.to_numeric(df_raw['下单数量'], errors='coerce').fillna(0)
        unit_p = pd.to_numeric(df_raw['折后供货价'], errors='coerce').fillna(0)
        
        # 优先取“总销售额”，若为空或0则用 折后供货价 * 下单数量 计算
        raw_sales = pd.to_numeric(df_raw.get('总销售额', 0), errors='coerce').fillna(0)
        sales = np.where(raw_sales > 0, raw_sales, qty * unit_p)

        # 从 VDF 字典映射品牌、属性、定位
        brands = [sku_meta_map.get(s, {}).get('品牌', 'DF品牌(待补充)') for s in skus]
        cats = [sku_meta_map.get(s, {}).get('产品属性', 'DF通用属性') for s in skus]
        pos = [sku_meta_map.get(s, {}).get('产品定位', '常规款') for s in skus]

        df_norm = pd.DataFrame({
            'Channel': 'DF',
            'Date': pd.to_datetime(df_raw['订单创建日期'], errors='coerce'),
            'SKU': skus,
            'Product_Name': df_raw.get('产品描述', ''),
            'Brand': brands,
            'Category': cats,
            'Positioning': pos,
            'Owner': df_raw.get('运营', '未分配').fillna('未分配'),
            'Qty': qty,
            'Sales': sales,
            'Unit_Price': unit_p
        })
        frames.append(df_norm)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.dropna(subset=['Date'])
    combined['Month'] = combined['Date'].dt.month
    combined['YearMonth'] = combined['Date'].dt.to_period('M').astype(str)
    return combined

def generate_demo_data():
    """生成演示数据以便未上传文件时预览工作台功能"""
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', '2026-12-31', freq='D')
    skus = [
        ('PD160B', 'Waykar', '除湿机', '主力爆款', 'VDF', 135.0, 'Kristen'),
        ('PD250B', 'Waykar', '除湿机', '利润款', 'DF', 168.0, 'Kristen'),
        ('KS-AC10', 'Kesnos', '移动空调', '季节款', 'VDF', 210.0, 'Zoey'),
        ('SR-MT12', 'Serweet', '床垫', '常青款', 'DF', 185.0, '陈菊'),
        ('YF-DH50', 'Yaufey', '商业除湿机', 'Pro工程款', 'VDF', 420.0, 'Kristen'),
    ]
    rows = []
    for d in dates:
        # 模拟季节性波动
        season_factor = 1.6 if d.month in [5, 6, 7, 8] else (0.7 if d.month in [1, 2, 12] else 1.0)
        for sku, brand, cat, pos, ch, price, owner in skus:
            if np.random.rand() < 0.35:
                q = int(np.random.poisson(3 * season_factor)) + 1
                rows.append({
                    'Channel': ch, 'Date': d, 'SKU': sku, 'Product_Name': f"{brand} {cat} {sku}",
                    'Brand': brand, 'Category': cat, 'Positioning': pos, 'Owner': owner,
                    'Qty': q, 'Sales': q * price, 'Unit_Price': price,
                    'Month': d.month, 'YearMonth': d.strftime('%Y-%m')
                })
    return pd.DataFrame(rows)

# ---------------------------------------------------------
# 3. 侧边栏：数据导入与全局控制参数
# ---------------------------------------------------------
with st.sidebar:
    st.header("📂 1. 2026 历史数据导入")
    vdf_uploaded = st.file_uploader("上传 2026 VDF 销售表 (xlsx/csv)", type=['xlsx', 'xls', 'csv'])
    df_uploaded = st.file_uploader("上传 2026 DF 销售表 (xlsx/csv)", type=['xlsx', 'xls', 'csv'])
    exclude_cancel = st.checkbox("VDF 自动剔除 Cancelled 订单", value=True)

    st.divider()
    st.header("⚙️ 2. 2027 全局基准参数")
    global_qty_growth = st.slider("全局销量基准增长率 (%)", -30, 200, 25, step=5)
    global_price_change = st.slider("全局平均供货价(ASP)调整 (%)", -20, 30, 0, step=1)
    seasonality_weight = st.slider(
        "2026 季节性曲线权重 (%)", 0, 100, 80, step=10,
        help="100%代表完全按2026年月度销量占比分配；0%代表全年12个月平均分配。"
    )
    
    st.divider()
    st.header("🔍 3. 维度筛选器")
    granularity = st.radio(
        "自定义调参颗粒度：",
        ["品牌 + 产品属性 (宏观规划)", "单品 SKU (精细化运营)", "运营负责人 (绩效拆解)"],
        index=0
    )

# 加载数据
if vdf_uploaded or df_uploaded:
    df_2026 = load_and_normalize_data(vdf_uploaded, df_uploaded, exclude_cancel)
    st.sidebar.success(f"成功加载 {len(df_2026):,} 条有效订单记录")
else:
    df_2026 = generate_demo_data()
    st.info("💡 当前处于**演示模式**（使用内置模拟数据）。请在左侧边栏上传你的 2026 年 VDF 和 DF 表格以启动真实测算。")

if df_2026.empty:
    st.error("未能解析到有效数据，请检查上传表格的列名是否与模板一致。")
    st.stop()

# 渠道与品牌过滤
col_f1, col_f2, col_f3 = st.columns(3)
with col_f1:
    sel_channels = st.multiselect("筛选渠道 (Channel)", df_2026['Channel'].unique(), default=list(df_2026['Channel'].unique()))
with col_f2:
    sel_brands = st.multiselect("筛选品牌 (Brand)", df_2026['Brand'].unique(), default=list(df_2026['Brand'].unique()))
with col_f3:
    sel_cats = st.multiselect("筛选产品属性 (Category)", df_2026['Category'].unique(), default=list(df_2026['Category'].unique()))

filtered_2026 = df_2026[
    (df_2026['Channel'].isin(sel_channels)) &
    (df_2026['Brand'].isin(sel_brands)) &
    (df_2026['Category'].isin(sel_cats))
].copy()

# ---------------------------------------------------------
# 4. 构建可编辑的 2027 预估参数矩阵
# ---------------------------------------------------------
st.title("🚀 2027 Home Depot 销量与销售额预估工作台")

# 确定分组键
if granularity == "品牌 + 产品属性 (宏观规划)":
    group_cols = ['Channel', 'Brand', 'Category']
elif granularity == "单品 SKU (精细化运营)":
    group_cols = ['Channel', 'Brand', 'Category', 'SKU', 'Positioning']
else:
    group_cols = ['Channel', 'Owner', 'Brand']

# 聚合 2026 基准数据
base_summary = filtered_2026.groupby(group_cols, as_index=False).agg(
    Qty_2026=('Qty', 'sum'),
    Sales_2026=('Sales', 'sum')
)
base_summary['ASP_2026'] = np.where(base_summary['Qty_2026'] > 0, base_summary['Sales_2026'] / base_summary['Qty_2026'], 0)

# 初始化 2027 默认预估参数列
base_summary['2027_销量增长率(%)'] = float(global_qty_growth)
base_summary['2027_新品增量(台)'] = 0
base_summary['2027_预期供货价($)'] = (base_summary['ASP_2026'] * (1 + global_price_change / 100)).round(2)

st.subheader("🎛️ 第一步：自定义 2027 业务增长与定价参数（支持直接双击单元格修改）")
st.caption("提示：修改下表中的【2027_销量增长率(%)】、【2027_新品增量(台)】或【2027_预期供货价($)】，下方所有图表与月度拆解将实时联动更新。")

# 配置列格式
column_config = {
    "Qty_2026": st.column_config.NumberColumn("2026 销量(台)", format="%d", disabled=True),
    "Sales_2026": st.column_config.NumberColumn("2026 供货额($)", format="$%.2f", disabled=True),
    "ASP_2026": st.column_config.NumberColumn("2026 均价($)", format="$%.2f", disabled=True),
    "2027_销量增长率(%)": st.column_config.NumberColumn("2027 销量增长率(%)", min_value=-100.0, max_value=1000.0, step=1.0, format="%.1f%%"),
    "2027_新品增量(台)": st.column_config.NumberColumn("2027 额外增量(台)", min_value=0, step=10, format="%d"),
    "2027_预期供货价($)": st.column_config.NumberColumn("2027 预期供货价($)", min_value=0.0, step=1.0, format="$%.2f"),
}

edited_plan = st.data_editor(
    base_summary,
    column_config=column_config,
    use_container_width=True,
    num_rows="dynamic",  # 允许用户直接在表格底部添加全新的 2027 新品行！
    key="forecast_editor"
)

# ---------------------------------------------------------
# 5. 核心预测计算与月度季节性拆解
# ---------------------------------------------------------
# 处理用户新增行（2026数据为NaN的情况）
edited_plan['Qty_2026'] = edited_plan['Qty_2026'].fillna(0)
edited_plan['Sales_2026'] = edited_plan['Sales_2026'].fillna(0)
edited_plan['2027_销量增长率(%)'] = edited_plan['2027_销量增长率(%)'].fillna(0)
edited_plan['2027_新品增量(台)'] = edited_plan['2027_新品增量(台)'].fillna(0)
edited_plan['2027_预期供货价($)'] = edited_plan['2027_预期供货价($)'].fillna(0)

# 计算 2027 预估总量
edited_plan['Qty_2027'] = np.round(
    edited_plan['Qty_2026'] * (1 + edited_plan['2027_销量增长率(%)'] / 100.0) + edited_plan['2027_新品增量(台)']
).astype(int)
edited_plan['Sales_2027'] = edited_plan['Qty_2027'] * edited_plan['2027_预期供货价($)']

# KPI 汇总看板
tot_qty_26 = edited_plan['Qty_2026'].sum()
tot_qty_27 = edited_plan['Qty_2027'].sum()
qty_yoy = ((tot_qty_27 - tot_qty_26) / tot_qty_26 * 100) if tot_qty_26 > 0 else 0

tot_sales_26 = edited_plan['Sales_2026'].sum()
tot_sales_27 = edited_plan['Sales_2027'].sum()
sales_yoy = ((tot_sales_27 - tot_sales_26) / tot_sales_26 * 100) if tot_sales_26 > 0 else 0

asp_26 = tot_sales_26 / tot_qty_26 if tot_qty_26 > 0 else 0
asp_27 = tot_sales_27 / tot_qty_27 if tot_qty_27 > 0 else 0
asp_yoy = ((asp_27 - asp_26) / asp_26 * 100) if asp_26 > 0 else 0

st.divider()
st.subheader("📈 第二步：2027 全年预估核心指标总览")

m1, m2, m3, m4 = st.columns(4)
m1.metric("2027 预估总销量 (Units)", f"{tot_qty_27:,.0f}", f"{qty_yoy:+.1f}% vs 2026 ({tot_qty_26:,.0f})")
m2.metric("2027 预估总供货额 (GMV/Cost)", f"${tot_sales_27:,.2f}", f"{sales_yoy:+.1f}% vs 2026 (${tot_sales_26:,.0f})")
m3.metric("2027 综合平均供货价 (ASP)", f"${asp_27:,.2f}", f"{asp_yoy:+.1f}% vs 2026 (${asp_26:,.2f})")
m4.metric("净增供货额贡献 ($)", f"+${(tot_sales_27 - tot_sales_26):,.2f}", f"净增 {tot_qty_27 - tot_qty_26:+,} 台")

# ---------------------------------------------------------
# 6. 月度季节性拆解引擎 (1月 - 12月)
# ---------------------------------------------------------
# 计算每个分组在 2026 年的月度权重
monthly_26 = filtered_2026.groupby(group_cols + ['Month'], as_index=False)['Qty'].sum()
group_tot_26 = filtered_2026.groupby(group_cols, as_index=False)['Qty'].sum().rename(columns={'Qty': 'Group_Total_26'})
monthly_26 = monthly_26.merge(group_tot_26, on=group_cols, how='left')
monthly_26['Hist_Weight'] = np.where(monthly_26['Group_Total_26'] > 0, monthly_26['Qty'] / monthly_26['Group_Total_26'], 1/12)

# 构造完整的 1-12 月笛卡尔积网格
months_df = pd.DataFrame({'Month': range(1, 13)})
plan_with_months = edited_plan.merge(months_df, how='cross')

# 合并历史月度权重
merge_keys = [c for c in group_cols if c in monthly_26.columns]
plan_with_months = plan_with_months.merge(
    monthly_26[merge_keys + ['Month', 'Hist_Weight', 'Qty']].rename(columns={'Qty': 'Month_Qty_2026'}),
    on=merge_keys + ['Month'],
    how='left'
)
plan_with_months['Hist_Weight'] = plan_with_months['Hist_Weight'].fillna(1/12)
plan_with_months['Month_Qty_2026'] = plan_with_months['Month_Qty_2026'].fillna(0)

# 融合季节性权重与平均权重
w = seasonality_weight / 100.0
plan_with_months['Blended_Weight'] = w * plan_with_months['Hist_Weight'] + (1 - w) * (1 / 12.0)

# 归一化权重确保12个月总和严格等于1
weight_sum = plan_with_months.groupby(group_cols)['Blended_Weight'].transform('sum')
plan_with_months['Final_Weight'] = plan_with_months['Blended_Weight'] / weight_sum

# 计算 2027 每月销量与销售额
plan_with_months['Month_Qty_2027'] = np.round(plan_with_months['Qty_2027'] * plan_with_months['Final_Weight']).astype(int)
plan_with_months['Month_Sales_2027'] = plan_with_months['Month_Qty_2027'] * plan_with_months['2027_预期供货价($)']

# 合并 2026 月度销售额用于对比
monthly_sales_26 = filtered_2026.groupby(['Month'], as_index=False).agg(Sales_2026=('Sales', 'sum'), Qty_2026=('Qty', 'sum'))
monthly_sales_27 = plan_with_months.groupby(['Month'], as_index=False).agg(Sales_2027=('Month_Sales_2027', 'sum'), Qty_2027=('Month_Qty_2027', 'sum'))
monthly_comp = monthly_sales_26.merge(monthly_sales_27, on='Month', how='outer').fillna(0).sort_values('Month')
monthly_comp['Month_Label'] = monthly_comp['Month'].astype(str) + '月'

# ---------------------------------------------------------
# 7. 可视化分析图表
# ---------------------------------------------------------
st.subheader("📊 第三步：月度节奏与渠道结构可视化")
tab1, tab2, tab3 = st.tabs(["📅 2026 vs 2027 月度供货额对比", "📦 VDF vs DF 双渠道结构拆解", "📋 1-12月明细透视表与导出"])

with tab1:
    fig_month = go.Figure()
    fig_month.add_trace(go.Bar(
        x=monthly_comp['Month_Label'], y=monthly_comp['Sales_2026'],
        name='2026 实际供货额 ($)', marker_color='#94a3b8'
    ))
    fig_month.add_trace(go.Bar(
        x=monthly_comp['Month_Label'], y=monthly_comp['Sales_2027'],
        name='2027 预估供货额 ($)', marker_color='#f97316'
    ))
    fig_month.update_layout(
        barmode='group', title="2026 实际 vs 2027 预估月度供货额趋势 ($)",
        xaxis_title="月份", yaxis_title="供货销售额 ($)", hovermode="x unified", height=420
    )
    st.plotly_chart(fig_month, use_container_width=True)

with tab2:
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        ch_comp = edited_plan.groupby('Channel', as_index=False)[['Sales_2026', 'Sales_2027']].sum()
        fig_ch = px.pie(ch_comp, values='Sales_2027', names='Channel', title="2027 预估供货额渠道占比 (VDF vs DF)", hole=0.4)
        st.plotly_chart(fig_ch, use_container_width=True)
    with col_c2:
        brand_comp = edited_plan.groupby('Brand', as_index=False)[['Sales_2026', 'Sales_2027']].sum().sort_values('Sales_2027', ascending=False)
        fig_br = px.bar(brand_comp, x='Brand', y=['Sales_2026', 'Sales_2027'], barmode='group', title="各品牌 2026 vs 2027 供货额对比")
        st.plotly_chart(fig_br, use_container_width=True)

with tab3:
    st.markdown("#### 2027 分月度供货销售额预估宽表 ($)")
    pivot_sales = plan_with_months.pivot_table(
        index=group_cols, columns='Month', values='Month_Sales_2027', aggfunc='sum', fill_value=0
    )
    pivot_sales.columns = [f"{m}月" for m in pivot_sales.columns]
    pivot_sales['2027全年合计($)'] = pivot_sales.sum(axis=1)
    st.dataframe(pivot_sales.style.format("${:,.2f}"), use_container_width=True)

    # 导出 Excel 功能（包含：参数汇总表、月度销售额表、月度销量表）
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        edited_plan.to_excel(writer, sheet_name='2027年度汇总与参数', index=False)
        pivot_sales.to_excel(writer, sheet_name='2027月度供货额预估($)')
        
        pivot_qty = plan_with_months.pivot_table(
            index=group_cols, columns='Month', values='Month_Qty_2027', aggfunc='sum', fill_value=0
        )
        pivot_qty.columns = [f"{m}月" for m in pivot_qty.columns]
        pivot_qty['2027全年合计(台)'] = pivot_qty.sum(axis=1)
        pivot_qty.to_excel(writer, sheet_name='2027月度销量预估(台)')

    st.download_button(
        label="📥 一键导出 2027 完整预估报表 (Excel)",
        data=output.getvalue(),
        file_name="2027_HomeDepot_Sales_Forecast.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
