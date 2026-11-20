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
    page_title="2027 HD 单品SKU月度销量与销售额预估工作台",
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

MONTH_COLS_26 = [f"26_{m}月" for m in range(1, 13)]
MONTH_COLS_27 = [f"27_{m}月" for m in range(1, 13)]

# ---------------------------------------------------------
# 2. 数据清洗与双渠道 SKU 对齐引擎（VDF: 产品SKU | DF: 发货产品）
# ---------------------------------------------------------
@st.cache_data
def load_and_normalize_data(vdf_file, df_file, exclude_cancelled=True):
    frames = []
    sku_meta_map = {}

    # --- 处理 VDF 数据（主 SKU 字段：产品SKU） ---
    if vdf_file is not None:
        vdf = pd.read_excel(vdf_file) if vdf_file.name.endswith(('xlsx', 'xls')) else pd.read_csv(vdf_file)
        vdf.columns = vdf.columns.str.strip()
        
        if exclude_cancelled and 'Line Status' in vdf.columns:
            vdf = vdf[~vdf['Line Status'].astype(str).str.lower().isin(['cancelled', 'canceled', 'rejected'])]
        
        meta_cols = [c for c in ['产品SKU', 'Vendor SKU', '品牌', '产品属性', '产品名称', '产品定位'] if c in vdf.columns]
        if '产品SKU' in meta_cols:
            meta_df = vdf[meta_cols].dropna(subset=['产品SKU']).copy()
            meta_df['产品SKU'] = meta_df['产品SKU'].astype(str).str.strip()
            for _, row in meta_df.iterrows():
                info = {
                    '品牌': row.get('品牌', 'Unknown') if pd.notna(row.get('品牌')) else 'Unknown',
                    '产品属性': row.get('产品属性', '未分类') if pd.notna(row.get('产品属性')) else '未分类',
                    '产品名称': row.get('产品名称', '') if pd.notna(row.get('产品名称')) else '',
                    '产品定位': row.get('产品定位', '常规款') if pd.notna(row.get('产品定位')) else '常规款',
                }
                sku_meta_map[row['产品SKU']] = info
                if 'Vendor SKU' in row and pd.notna(row['Vendor SKU']):
                    v_sku = str(row['Vendor SKU']).strip()
                    if v_sku and v_sku not in sku_meta_map:
                        sku_meta_map[v_sku] = info

        vdf_norm = pd.DataFrame({
            'Channel': 'VDF',
            'Date': pd.to_datetime(vdf['Order Date'], errors='coerce'),
            'SKU': vdf['产品SKU'].astype(str).str.strip(),
            'Product_Name': vdf.get('产品名称', vdf.get('Description', '')).fillna(''),
            'Brand': vdf.get('品牌', 'Unknown').fillna('Unknown'),
            'Category': vdf.get('产品属性', '未分类').fillna('未分类'),
            'Positioning': vdf.get('产品定位', '常规款').fillna('常规款'),
            'Owner': vdf.get('运营', '未分配').fillna('未分配'),
            'Qty': pd.to_numeric(vdf['Quantity'], errors='coerce').fillna(0),
            'Sales': pd.to_numeric(vdf['Total Cost'], errors='coerce').fillna(0),
            'Unit_Price': pd.to_numeric(vdf['Unit Cost'], errors='coerce').fillna(0)
        })
        frames.append(vdf_norm)

    # --- 处理 DF 数据（核心口径：发货产品，缺失时回退 产品SKU） ---
    if df_file is not None:
        df_raw = pd.read_excel(df_file) if df_file.name.endswith(('xlsx', 'xls')) else pd.read_csv(df_file)
        df_raw.columns = df_raw.columns.str.strip()
        
        ship_sku_raw = df_raw['发货产品'].astype(str).str.strip() if '发货产品' in df_raw.columns else pd.Series(['']*len(df_raw))
        fallback_sku_raw = df_raw['产品SKU'].astype(str).str.strip() if '产品SKU' in df_raw.columns else pd.Series(['']*len(df_raw))
        
        skus = np.where(
            (ship_sku_raw != '') & (~ship_sku_raw.str.lower().isin(['nan', 'none', 'null'])),
            ship_sku_raw,
            fallback_sku_raw
        )
        skus = pd.Series(skus, index=df_raw.index).astype(str).str.strip()

        # 若 DF 表中自身带有“产品SKU”与“产品属性/品牌”对应关系，补充进映射字典
        if '产品SKU' in df_raw.columns:
            for i in range(len(df_raw)):
                s_ship = skus.iloc[i]
                s_orig = fallback_sku_raw.iloc[i]
                if s_orig and s_orig in sku_meta_map and s_ship and s_ship not in sku_meta_map:
                    sku_meta_map[s_ship] = sku_meta_map[s_orig]

        qty = pd.to_numeric(df_raw['下单数量'], errors='coerce').fillna(0)
        unit_p = pd.to_numeric(df_raw['折后供货价'], errors='coerce').fillna(0)
        
        raw_sales = pd.to_numeric(df_raw.get('总销售额', 0), errors='coerce').fillna(0)
        sales = np.where(raw_sales > 0, raw_sales, qty * unit_p)

        brands = [sku_meta_map.get(s, {}).get('品牌', 'DF品牌(待归类)') for s in skus]
        cats = [sku_meta_map.get(s, {}).get('产品属性', 'DF通用属性') for s in skus]
        pos = [sku_meta_map.get(s, {}).get('产品定位', '常规款') for s in skus]
        desc_series = df_raw.get('产品描述', pd.Series(['']*len(skus)))
        names = [
            desc_series.iloc[i]
            if pd.notna(desc_series.iloc[i]) and str(desc_series.iloc[i]).strip() != ''
            else sku_meta_map.get(skus.iloc[i], {}).get('产品名称', '')
            for i in range(len(skus))
        ]

        df_norm = pd.DataFrame({
            'Channel': 'DF',
            'Date': pd.to_datetime(df_raw['订单创建日期'], errors='coerce'),
            'SKU': skus,
            'Product_Name': names,
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
    combined = combined[(combined['SKU'] != '') & (~combined['SKU'].str.lower().isin(['nan', 'none', 'null']))]
    combined['Month'] = combined['Date'].dt.month
    return combined

def generate_demo_data():
    """生成演示数据（VDF 为 产品SKU，DF 为 发货产品）"""
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', '2026-12-31', freq='D')
    skus = [
        ('PD160B', 'Waykar 34 Pint 除湿机', 'Waykar', '除湿机', '主力爆款', 'VDF', 135.0, 'Kristen'),
        ('PD250B-SHIP', 'Waykar 40 Pint 除湿机(发货SKU)', 'Waykar', '除湿机', '利润款', 'DF', 168.0, 'Kristen'),
        ('PD250B', 'Waykar 40 Pint 除湿机', 'Waykar', '除湿机', '利润款', 'VDF', 165.0, 'Kristen'),
        ('KS-AC10', 'Kesnos 10000 BTU 移动空调', 'Kesnos', '移动空调', '季节款', 'VDF', 210.0, 'Zoey'),
        ('SR-MT12-SHIP', 'Serweet 12寸混合床垫 Queen(发货SKU)', 'Serweet', '床垫', '常青款', 'DF', 185.0, '陈菊'),
        ('YF-DH50', 'Yaufey 商业除湿机 120 Pint', 'Yaufey', '商业除湿机', 'Pro工程款', 'VDF', 420.0, 'Kristen'),
    ]
    rows = []
    for d in dates:
        for sku, name, brand, cat, pos, ch, price, owner in skus:
            if cat in ['除湿机', '商业除湿机']:
                season_factor = 1.8 if d.month in [5, 6, 7, 8] else (0.6 if d.month in [1, 2, 12] else 1.0)
            elif cat == '移动空调':
                season_factor = 2.5 if d.month in [6, 7, 8] else (0.1 if d.month in [1, 2, 11, 12] else 0.7)
            else:
                season_factor = 1.2 if d.month in [5, 9, 11] else 0.9

            if np.random.rand() < 0.35:
                q = max(1, int(np.random.poisson(2.5 * season_factor)))
                rows.append({
                    'Channel': ch, 'Date': d, 'SKU': sku, 'Product_Name': name,
                    'Brand': brand, 'Category': cat, 'Positioning': pos, 'Owner': owner,
                    'Qty': q, 'Sales': q * price, 'Unit_Price': price, 'Month': d.month
                })
    return pd.DataFrame(rows)

# ---------------------------------------------------------
# 3. 侧边栏：数据导入与全局初始化参数
# ---------------------------------------------------------
with st.sidebar:
    st.header("📂 1. 2026 历史数据导入")
    vdf_uploaded = st.file_uploader("上传 2026 VDF 销售表 (按 产品SKU)", type=['xlsx', 'xls', 'csv'])
    df_uploaded = st.file_uploader("上传 2026 DF 销售表 (按 发货产品)", type=['xlsx', 'xls', 'csv'])
    exclude_cancel = st.checkbox("VDF 自动剔除 Cancelled 订单", value=True)
    st.caption("口径说明：VDF 取 `产品SKU`，DF 取 `发货产品`。")

    st.divider()
    st.header("⚙️ 2. 全局初始化基线 (用于快速生成初稿)")
    init_growth = st.slider("默认月度同比增速基线 (%)", -50, 200, 20, step=5,
                            help="系统会先按此增速生成每个SKU 1-12月的初始预估值，随后你可在主表逐个SKU、逐个月份手动修改。")
    init_price_adj = st.slider("默认供货价调整基线 (%)", -20, 30, 0, step=1)
    
    st.divider()
    st.header("🔍 3. 视图显示设置")
    show_2026_months = st.checkbox("在预估表中同步展示 2026 1-12月历史参考列", value=False,
                                   help="开启后可在同一张表里左右对照 2026 实际月销与 2027 预估月销。")

# 加载数据
if vdf_uploaded or df_uploaded:
    df_2026 = load_and_normalize_data(vdf_uploaded, df_uploaded, exclude_cancel)
    st.sidebar.success(f"已加载 {len(df_2026):,} 条有效订单，共 {df_2026['SKU'].nunique()} 个独立 SKU")
else:
    df_2026 = generate_demo_data()
    st.info("💡 当前处于**演示模式**。请在左侧上传 2026 年 VDF（产品SKU）与 DF（发货产品）销售表，即可按单品月度销量进行自定义预估。")

if df_2026.empty:
    st.error("未能解析到有效数据，请检查上传文件表头（VDF需含 产品SKU，DF需含 发货产品）。")
    st.stop()

# ---------------------------------------------------------
# 4. 顶部多维筛选器（定位特定 SKU / 品牌 / 运营）
# ---------------------------------------------------------
st.title("🎯 2027 单品 SKU 月度销量与销售额预估工作台")

f1, f2, f3, f4, f5 = st.columns([1.2, 1.2, 1.2, 1.2, 1.8])
with f1:
    sel_ch = st.multiselect("渠道 (Channel)", df_2026['Channel'].unique(), default=list(df_2026['Channel'].unique()))
with f2:
    sel_brand = st.multiselect("品牌 (Brand)", df_2026['Brand'].unique(), default=list(df_2026['Brand'].unique()))
with f3:
    sel_cat = st.multiselect("产品属性 (Category)", df_2026['Category'].unique(), default=list(df_2026['Category'].unique()))
with f4:
    sel_owner = st.multiselect("运营 (Owner)", df_2026['Owner'].unique(), default=list(df_2026['Owner'].unique()))
with f5:
    sku_keyword = st.text_input("🔎 搜索 SKU（VDF产品SKU / DF发货产品）或品名", placeholder="输入 SKU 编码过滤...")

filtered_2026 = df_2026[
    (df_2026['Channel'].isin(sel_ch)) &
    (df_2026['Brand'].isin(sel_brand)) &
    (df_2026['Category'].isin(sel_cat)) &
    (df_2026['Owner'].isin(sel_owner))
].copy()

if sku_keyword.strip():
    kw = sku_keyword.strip().lower()
    filtered_2026 = filtered_2026[
        filtered_2026['SKU'].str.lower().str.contains(kw) |
        filtered_2026['Product_Name'].str.lower().str.contains(kw)
    ]

# ---------------------------------------------------------
# 5. 构建【单品 SKU × 1-12月】基准宽表
# ---------------------------------------------------------
sku_keys = ['Channel', 'SKU', 'Brand', 'Category', 'Owner']

sku_base = filtered_2026.groupby(sku_keys, as_index=False).agg(
    Product_Name=('Product_Name', 'first'),
    Positioning=('Positioning', 'first'),
    Qty_2026=('Qty', 'sum'),
    Sales_2026=('Sales', 'sum')
)
sku_base['ASP_2026'] = np.where(sku_base['Qty_2026'] > 0, (sku_base['Sales_2026'] / sku_base['Qty_2026']).round(2), 0.0)

pivot_26 = filtered_2026.pivot_table(
    index=sku_keys, columns='Month', values='Qty', aggfunc='sum', fill_value=0
)
for m in range(1, 13):
    if m not in pivot_26.columns:
        pivot_26[m] = 0
pivot_26 = pivot_26[list(range(1, 13))]
pivot_26.columns = MONTH_COLS_26
pivot_26 = pivot_26.reset_index()

master_df = sku_base.merge(pivot_26, on=sku_keys, how='left')

master_df['2027_预期供货价($)'] = (master_df['ASP_2026'] * (1 + init_price_adj / 100.0)).round(2)

for m in range(1, 13):
    col_26 = f"26_{m}月"
    col_27 = f"27_{m}月"
    master_df[col_27] = np.round(master_df[col_26] * (1 + init_growth / 100.0)).astype(int)

# ---------------------------------------------------------
# 6. 交互式单品 SKU 月度预估编辑区
# ---------------------------------------------------------
st.subheader("📝 第一步：单品 SKU 2027 年 1–12 月销量自定义编辑表")
st.markdown("""
- **SKU 口径说明**：`VDF` 对应表内 **`产品SKU`**，`DF` 对应表内 **`发货产品`**。
- **直接修改月度销量**：双击任意 SKU 的 **`27_1月` ~ `27_12月`** 单元格输入目标台数（支持从 Excel 复制粘贴）。
- **新增 2027 新品 SKU**：拉到表格最底部点击 **`+`** 行，填写 `Channel`、`SKU`、`Brand`、`Category`、`2027_预期供货价($)` 及各月预估销量即可。
""")

display_cols = ['Channel', 'SKU', 'Brand', 'Category', 'Owner', 'ASP_2026', '2027_预期供货价($)', 'Qty_2026']
if show_2026_months:
    display_cols += MONTH_COLS_26
display_cols += MONTH_COLS_27

col_cfg = {
    "Channel": st.column_config.SelectboxColumn("渠道", options=["VDF", "DF"], required=True, width="small"),
    "SKU": st.column_config.TextColumn("SKU (VDF产品/DF发货)", required=True, width="medium"),
    "Brand": st.column_config.TextColumn("品牌", width="small"),
    "Category": st.column_config.TextColumn("产品属性", width="small"),
    "Owner": st.column_config.TextColumn("运营", width="small"),
    "ASP_2026": st.column_config.NumberColumn("26均价($)", format="$%.2f", disabled=True),
    "2027_预期供货价($)": st.column_config.NumberColumn("27预期供货价($)", min_value=0.0, step=1.0, format="$%.2f"),
    "Qty_2026": st.column_config.NumberColumn("26总销量", format="%d", disabled=True),
}
for c26 in MONTH_COLS_26:
    col_cfg[c26] = st.column_config.NumberColumn(c26, format="%d", disabled=True)
for c27 in MONTH_COLS_27:
    col_cfg[c27] = st.column_config.NumberColumn(c27, min_value=0, step=1, format="%d")

edited_sku_df = st.data_editor(
    master_df[display_cols],
    column_config=col_cfg,
    use_container_width=True,
    height=460,
    num_rows="dynamic",
    key="sku_monthly_editor"
)

# ---------------------------------------------------------
# 7. 实时重算单品与全局 2027 汇总指标
# ---------------------------------------------------------
for c27 in MONTH_COLS_27:
    edited_sku_df[c27] = pd.to_numeric(edited_sku_df[c27], errors='coerce').fillna(0).astype(int)
edited_sku_df['2027_预期供货价($)'] = pd.to_numeric(edited_sku_df['2027_预期供货价($)'], errors='coerce').fillna(0.0)
edited_sku_df['Qty_2026'] = pd.to_numeric(edited_sku_df['Qty_2026'], errors='coerce').fillna(0).astype(int)

calc_df = edited_sku_df.copy()
if not show_2026_months:
    calc_df = calc_df.merge(
        master_df[['Channel', 'SKU', 'Sales_2026'] + MONTH_COLS_26],
        on=['Channel', 'SKU'],
        how='left'
    )
    calc_df['Sales_2026'] = calc_df['Sales_2026'].fillna(0.0)
    for c26 in MONTH_COLS_26:
        calc_df[c26] = calc_df[c26].fillna(0).astype(int)
else:
    calc_df = calc_df.merge(
        master_df[['Channel', 'SKU', 'Sales_2026']],
        on=['Channel', 'SKU'],
        how='left'
    )
    calc_df['Sales_2026'] = calc_df['Sales_2026'].fillna(0.0)

calc_df['Qty_2027'] = calc_df[MONTH_COLS_27].sum(axis=1)
calc_df['Sales_2027'] = calc_df['Qty_2027'] * calc_df['2027_预期供货价($)']
calc_df['销量同比(%)'] = np.where(
    calc_df['Qty_2026'] > 0,
    ((calc_df['Qty_2027'] - calc_df['Qty_2026']) / calc_df['Qty_2026'] * 100).round(1),
    np.nan
)

SALES_COLS_27 = [f"27_{m}月_供货额($)" for m in range(1, 13)]
for m in range(1, 13):
    calc_df[f"27_{m}月_供货额($)"] = calc_df[f"27_{m}月"] * calc_df['2027_预期供货价($)']

tot_q26 = calc_df['Qty_2026'].sum()
tot_q27 = calc_df['Qty_2027'].sum()
q_yoy = ((tot_q27 - tot_q26) / tot_q26 * 100) if tot_q26 > 0 else 0

tot_s26 = calc_df['Sales_2026'].sum()
tot_s27 = calc_df['Sales_2027'].sum()
s_yoy = ((tot_s27 - tot_s26) / tot_s26 * 100) if tot_s26 > 0 else 0

asp_26 = tot_s26 / tot_q26 if tot_q26 > 0 else 0
asp_27 = tot_s27 / tot_q27 if tot_q27 > 0 else 0
asp_yoy = ((asp_27 - asp_26) / asp_26 * 100) if asp_26 > 0 else 0

st.divider()
st.subheader("📊 第二步：基于单品汇总的 2027 全年核心预测指标")
k1, k2, k3, k4 = st.columns(4)
k1.metric("2027 预估总销量 (Units)", f"{tot_q27:,.0f}", f"{q_yoy:+.1f}% vs 2026 ({tot_q26:,.0f})")
k2.metric("2027 预估总供货额 ($)", f"${tot_s27:,.2f}", f"{s_yoy:+.1f}% vs 2026 (${tot_s26:,.0f})")
k3.metric("2027 综合供货均价 (ASP)", f"${asp_27:,.2f}", f"{asp_yoy:+.1f}% vs 2026 (${asp_26:,.2f})")
k4.metric("活跃预估 SKU 数量", f"{calc_df['SKU'].nunique()} 个", f"总行数 {len(calc_df)} (含双渠道)")

# ---------------------------------------------------------
# 8. 可视化分析与单品明细报表导出
# ---------------------------------------------------------
tab1, tab2, tab3 = st.tabs([
    "📈 1-12月销量与供货额趋势对比",
    "🏆 单品 SKU 贡献度排行 (Top SKU)",
    "📋 单品汇总表 & Excel 完整导出"
])

with tab1:
    m_labels = [f"{m}月" for m in range(1, 13)]
    q26_monthly = [calc_df[f"26_{m}月"].sum() for m in range(1, 13)]
    q27_monthly = [calc_df[f"27_{m}月"].sum() for m in range(1, 13)]
    s27_monthly = [calc_df[f"27_{m}月_供货额($)"].sum() for m in range(1, 13)]

    c_chart1, c_chart2 = st.columns(2)
    with c_chart1:
        fig_q = go.Figure()
        fig_q.add_trace(go.Bar(x=m_labels, y=q26_monthly, name='2026 实际销量(台)', marker_color='#94a3b8'))
        fig_q.add_trace(go.Bar(x=m_labels, y=q27_monthly, name='2027 预估销量(台)', marker_color='#2563eb'))
        fig_q.update_layout(barmode='group', title="2026 vs 2027 月度总销量对比 (台)", hovermode="x unified", height=380)
        st.plotly_chart(fig_q, use_container_width=True)

    with c_chart2:
        fig_s = go.Figure()
        fig_s.add_trace(go.Scatter(
            x=m_labels, y=s27_monthly, mode='lines+markers+text',
            text=[f"${v/1000:.0f}k" for v in s27_monthly], textposition="top center",
            name='2027 预估供货额($)', line=dict(color='#f97316', width=3)
        ))
        fig_s.update_layout(title="2027 预估月度供货销售额曲线 ($)", hovermode="x unified", height=380)
        st.plotly_chart(fig_s, use_container_width=True)

with tab2:
    sku_rank = calc_df.groupby(['SKU', 'Brand', 'Category'], as_index=False)[['Qty_2026', 'Qty_2027', 'Sales_2026', 'Sales_2027']].sum()
    sku_rank = sku_rank.sort_values('Sales_2027', ascending=False).head(15)
    
    fig_top = px.bar(
        sku_rank, x='SKU', y=['Sales_2026', 'Sales_2027'], barmode='group',
        title="Top 15 单品 SKU：2026 实际 vs 2027 预估供货额 ($)",
        hover_data=['Brand', 'Category']
    )
    st.plotly_chart(fig_top, use_container_width=True)

with tab3:
    summary_view_cols = [
        'Channel', 'SKU', 'Brand', 'Category', 'Owner',
        'Qty_2026', 'Qty_2027', '销量同比(%)',
        'ASP_2026', '2027_预期供货价($)', 'Sales_2026', 'Sales_2027'
    ]
    st.dataframe(
        calc_df[summary_view_cols].sort_values('Sales_2027', ascending=False).style.format({
            'Qty_2026': '{:,.0f}',
            'Qty_2027': '{:,.0f}',
            '销量同比(%)': '{:+.1f}%',
            'ASP_2026': '${:,.2f}',
            '2027_预期供货价($)': '${:,.2f}',
            'Sales_2026': '${:,.2f}',
            'Sales_2027': '${:,.2f}'
        }, na_rep="新品"),
        use_container_width=True
    )

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        calc_df[summary_view_cols].to_excel(writer, sheet_name='1_单品SKU年度预估总览', index=False)
        qty_detail_cols = ['Channel', 'SKU', 'Brand', 'Category', 'Owner', 'Qty_2026', 'Qty_2027'] + MONTH_COLS_26 + MONTH_COLS_27
        calc_df[qty_detail_cols].to_excel(writer, sheet_name='2_单品月度销量明细(台)', index=False)
        sales_detail_cols = ['Channel', 'SKU', 'Brand', 'Category', 'Owner', '2027_预期供货价($)', 'Sales_2027'] + SALES_COLS_27
        calc_df[sales_detail_cols].to_excel(writer, sheet_name='3_单品2027月度供货额($)', index=False)

    st.download_button(
        label="📥 导出 2027 单品 SKU 月度预估完整报表 (Excel)",
        data=output.getvalue(),
        file_name="2027_HD_SKU_Monthly_Forecast.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
