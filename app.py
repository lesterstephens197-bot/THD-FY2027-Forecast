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
    page_title="2027 HD VDF/DF 分渠道唯一SKU月度预估工作台",
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
SALES_COLS_26 = [f"26_{m}月_供货额($)" for m in range(1, 13)]
SALES_COLS_27 = [f"27_{m}月_供货额($)" for m in range(1, 13)]

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

        ship_sku_raw = df_raw['发货产品'].astype(str).str.strip() if '发货产品' in df_raw.columns else pd.Series([''] * len(df_raw))
        fallback_sku_raw = df_raw['产品SKU'].astype(str).str.strip() if '产品SKU' in df_raw.columns else pd.Series([''] * len(df_raw))

        skus = np.where(
            (ship_sku_raw != '') & (~ship_sku_raw.str.lower().isin(['nan', 'none', 'null'])),
            ship_sku_raw,
            fallback_sku_raw
        )
        skus = pd.Series(skus, index=df_raw.index).astype(str).str.strip()

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
        desc_series = df_raw.get('产品描述', pd.Series([''] * len(skus)))
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
    """生成演示数据（VDF 为 产品SKU，DF 为 发货产品；各自渠道内 SKU 唯一）"""
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', '2026-12-31', freq='D')
    records_cfg = [
        ('PD160B', 'Waykar 34 Pint 除湿机', 'Waykar', '除湿机', '主力爆款', 'VDF', 135.0, 'Kristen'),
        ('PD160B-DF', 'Waykar 34 Pint 除湿机(DF发货SKU)', 'Waykar', '除湿机', '主力爆款', 'DF', 138.0, 'Kristen'),
        ('PD250B-DF', 'Waykar 40 Pint 除湿机(DF发货SKU)', 'Waykar', '除湿机', '利润款', 'DF', 168.0, 'Kristen'),
        ('KS-AC10', 'Kesnos 10000 BTU 移动空调', 'Kesnos', '移动空调', '季节款', 'VDF', 210.0, 'Zoey'),
        ('SR-MT12-DF', 'Serweet 12寸混合床垫 Queen(DF发货SKU)', 'Serweet', '床垫', '常青款', 'DF', 185.0, '陈菊'),
        ('YF-DH50', 'Yaufey 商业除湿机 120 Pint', 'Yaufey', '商业除湿机', 'Pro工程款', 'VDF', 420.0, 'Kristen'),
    ]
    rows = []
    for d in dates:
        for sku, name, brand, cat, pos, ch, price, owner in records_cfg:
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


def build_channel_unique_sku_table(df_filtered):
    """按【Channel + 唯一 SKU】汇总：保证 VDF 内 SKU 唯一、DF 内 SKU 唯一"""
    if df_filtered.empty:
        return pd.DataFrame()

    keys = ['Channel', 'SKU']

    def first_non_empty(series):
        for v in series:
            if pd.notna(v) and str(v).strip() != '':
                return str(v).strip()
        return ''

    def join_unique(series):
        vals = sorted({str(v).strip() for v in series.dropna() if str(v).strip() != ''})
        return " / ".join(vals) if vals else "未分配"

    sku_agg = df_filtered.groupby(keys, as_index=False).agg(
        Product_Name=('Product_Name', first_non_empty),
        Brand=('Brand', first_non_empty),
        Category=('Category', first_non_empty),
        Positioning=('Positioning', first_non_empty),
        Owner=('Owner', join_unique),
        Qty_2026=('Qty', 'sum'),
        Sales_2026=('Sales', 'sum')
    )
    sku_agg['ASP_2026'] = np.where(sku_agg['Qty_2026'] > 0, (sku_agg['Sales_2026'] / sku_agg['Qty_2026']).round(2), 0.0)

    piv_qty = df_filtered.pivot_table(index=keys, columns='Month', values='Qty', aggfunc='sum', fill_value=0)
    for m in range(1, 13):
        if m not in piv_qty.columns:
            piv_qty[m] = 0
    piv_qty = piv_qty[list(range(1, 13))]
    piv_qty.columns = MONTH_COLS_26
    piv_qty = piv_qty.reset_index()

    piv_sales = df_filtered.pivot_table(index=keys, columns='Month', values='Sales', aggfunc='sum', fill_value=0)
    for m in range(1, 13):
        if m not in piv_sales.columns:
            piv_sales[m] = 0
    piv_sales = piv_sales[list(range(1, 13))]
    piv_sales.columns = SALES_COLS_26
    piv_sales = piv_sales.reset_index()

    out = sku_agg.merge(piv_qty, on=keys, how='left').merge(piv_sales, on=keys, how='left')
    # 严格保证每个 Channel 内 SKU 唯一
    out = out.drop_duplicates(subset=['Channel', 'SKU']).sort_values(['Channel', 'Sales_2026'], ascending=[True, False]).reset_index(drop=True)
    return out


def enforce_unique_sku_per_channel(df_in):
    """强制保证 VDF 内部 SKU 唯一、DF 内部 SKU 唯一"""
    df_clean = df_in.copy()
    df_clean['Channel'] = df_clean['Channel'].astype(str).str.strip().str.upper()
    df_clean['SKU'] = df_clean['SKU'].astype(str).str.strip()
    df_clean = df_clean[
        df_clean['Channel'].isin(['VDF', 'DF']) &
        (df_clean['SKU'] != '') &
        (~df_clean['SKU'].str.lower().isin(['nan', 'none', 'null']))
    ].copy()

    for c27 in MONTH_COLS_27:
        df_clean[c27] = pd.to_numeric(df_clean[c27], errors='coerce').fillna(0).astype(int)
    df_clean['2027_预期供货价($)'] = pd.to_numeric(df_clean['2027_预期供货价($)'], errors='coerce').fillna(0.0)
    df_clean['Qty_2026'] = pd.to_numeric(df_clean['Qty_2026'], errors='coerce').fillna(0).astype(int)

    dup_cnt = len(df_clean) - len(df_clean.drop_duplicates(subset=['Channel', 'SKU']))
    if dup_cnt > 0:
        agg_map = {
            'Brand': 'first',
            'Category': 'first',
            'Owner': 'first',
            'ASP_2026': 'first',
            '2027_预期供货价($)': 'mean',
            'Qty_2026': 'sum'
        }
        for c26 in MONTH_COLS_26:
            if c26 in df_clean.columns:
                agg_map[c26] = 'sum'
        for c27 in MONTH_COLS_27:
            agg_map[c27] = 'sum'
        df_clean = df_clean.groupby(['Channel', 'SKU'], as_index=False).agg(agg_map)

    return df_clean, dup_cnt


# ---------------------------------------------------------
# 3. 侧边栏：数据导入与全局初始化参数
# ---------------------------------------------------------
with st.sidebar:
    st.header("📂 1. 2026 历史数据导入")
    vdf_uploaded = st.file_uploader("上传 2026 VDF 销售表 (按 产品SKU)", type=['xlsx', 'xls', 'csv'])
    df_uploaded = st.file_uploader("上传 2026 DF 销售表 (按 发货产品)", type=['xlsx', 'xls', 'csv'])
    exclude_cancel = st.checkbox("VDF 自动剔除 Cancelled 订单", value=True)
    st.caption("规则：DF 与 VDF 分开统计与预估；VDF 内 `产品SKU` 唯一，DF 内 `发货产品` 唯一。")

    st.divider()
    st.header("⚙️ 2. 全局初始化基线 (用于快速生成初稿)")
    init_growth = st.slider("默认月度同比增速基线 (%)", -50, 200, 20, step=5,
                            help="系统会先按此增速生成每个渠道唯一 SKU 1-12月的初始预估值，随后可在主表逐 SKU、逐月手动修改。")
    init_price_adj = st.slider("默认供货价调整基线 (%)", -20, 30, 0, step=1)

    st.divider()
    st.header("🔍 3. 视图显示设置")
    show_2026_months = st.checkbox("在预估表中同步展示 2026 1-12月历史参考列", value=True,
                                   help="开启后可在同一张表里左右对照 2026 实际月销与 2027 预估月销。")

# 加载数据
if vdf_uploaded or df_uploaded:
    df_2026 = load_and_normalize_data(vdf_uploaded, df_uploaded, exclude_cancel)
    vdf_u = df_2026[df_2026['Channel'] == 'VDF']['SKU'].nunique()
    df_u = df_2026[df_2026['Channel'] == 'DF']['SKU'].nunique()
    st.sidebar.success(f"已加载 {len(df_2026):,} 条有效订单（VDF唯一SKU: {vdf_u} | DF唯一SKU: {df_u}）")
else:
    df_2026 = generate_demo_data()
    st.info("💡 当前处于**演示模式**。请在左侧上传 2026 年 VDF（产品SKU）与 DF（发货产品）销售表；DF 与 VDF 分开处理，各自渠道内 SKU 唯一。")

if df_2026.empty:
    st.error("未能解析到有效数据，请检查上传文件表头（VDF需含 产品SKU，DF需含 发货产品）。")
    st.stop()

# ---------------------------------------------------------
# 4. 顶部多维筛选器
# ---------------------------------------------------------
st.title("🎯 2026 分渠道销售数据 & 2027 VDF/DF 唯一 SKU 月度预估工作台")

f1, f2, f3, f4, f5 = st.columns([1.2, 1.2, 1.2, 1.2, 1.8])
with f1:
    sel_ch = st.multiselect("渠道 (Channel)", ['VDF', 'DF'], default=['VDF', 'DF'])
with f2:
    sel_brand = st.multiselect("品牌 (Brand)", df_2026['Brand'].unique(), default=list(df_2026['Brand'].unique()))
with f3:
    sel_cat = st.multiselect("产品属性 (Category)", df_2026['Category'].unique(), default=list(df_2026['Category'].unique()))
with f4:
    sel_owner = st.multiselect("运营 (Owner)", df_2026['Owner'].unique(), default=list(df_2026['Owner'].unique()))
with f5:
    sku_keyword = st.text_input("🔎 搜索产品SKU或品名", placeholder="输入 SKU 编码过滤...")

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

channel_unique_2026 = build_channel_unique_sku_table(filtered_2026)

# ---------------------------------------------------------
# 5. 2026 年历史销售数据展示区（VDF 与 DF 分开汇总）
# ---------------------------------------------------------
st.subheader("🕰️ 2026 年实际销售数据总览（VDF 与 DF 分开汇总，各自 SKU 唯一）")

vdf_26_rows = channel_unique_2026[channel_unique_2026['Channel'] == 'VDF'] if not channel_unique_2026.empty else pd.DataFrame()
df_26_rows = channel_unique_2026[channel_unique_2026['Channel'] == 'DF'] if not channel_unique_2026.empty else pd.DataFrame()

vdf_q26 = vdf_26_rows['Qty_2026'].sum() if not vdf_26_rows.empty else 0
vdf_s26 = vdf_26_rows['Sales_2026'].sum() if not vdf_26_rows.empty else 0
vdf_asp26 = vdf_s26 / vdf_q26 if vdf_q26 > 0 else 0.0
vdf_cnt26 = vdf_26_rows['SKU'].nunique() if not vdf_26_rows.empty else 0

df_q26 = df_26_rows['Qty_2026'].sum() if not df_26_rows.empty else 0
df_s26 = df_26_rows['Sales_2026'].sum() if not df_26_rows.empty else 0
df_asp26 = df_s26 / df_q26 if df_q26 > 0 else 0.0
df_cnt26 = df_26_rows['SKU'].nunique() if not df_26_rows.empty else 0

st.markdown("**VDF 渠道（口径：产品SKU 唯一）**")
vc1, vc2, vc3, vc4 = st.columns(4)
vc1.metric("VDF 2026 总销量", f"{vdf_q26:,.0f}", "台")
vc2.metric("VDF 2026 总供货额", f"${vdf_s26:,.2f}", "USD")
vc3.metric("VDF 2026 均价 (ASP)", f"${vdf_asp26:,.2f}", "供货额 / 销量")
vc4.metric("VDF 2026 唯一 SKU 数", f"{vdf_cnt26} 个", "VDF内唯一")

st.markdown("**DF 渠道（口径：发货产品 唯一）**")
dc1, dc2, dc3, dc4 = st.columns(4)
dc1.metric("DF 2026 总销量", f"{df_q26:,.0f}", "台")
dc2.metric("DF 2026 总供货额", f"${df_s26:,.2f}", "USD")
dc3.metric("DF 2026 均价 (ASP)", f"${df_asp26:,.2f}", "供货额 / 销量")
dc4.metric("DF 2026 唯一 SKU 数", f"{df_cnt26} 个", "DF内唯一")

with st.expander("📂 展开查看 2026 分渠道销售数据明细（月度趋势 · VDF/DF 唯一SKU月销表 · 清洗底表）", expanded=True):
    h_tab1, h_tab2, h_tab3 = st.tabs([
        "📈 2026 VDF vs DF 分月销量与供货额",
        "📦 2026 分渠道唯一 SKU 1-12月销量明细",
        "🧾 2026 清洗后订单底表"
    ])

    with h_tab1:
        m26_ch = filtered_2026.groupby(['Month', 'Channel'], as_index=False).agg(
            Qty_26=('Qty', 'sum'), Sales_26=('Sales', 'sum')
        ).sort_values('Month')
        m26_ch['Month_Label'] = m26_ch['Month'].astype(str) + '月'

        ch_g1, ch_g2 = st.columns(2)
        with ch_g1:
            fig_26_q = px.bar(
                m26_ch, x='Month_Label', y='Qty_26', color='Channel', barmode='group',
                title="2026 VDF vs DF 分月销量对比 (台)",
                color_discrete_map={'VDF': '#2563eb', 'DF': '#f97316'}
            )
            st.plotly_chart(fig_26_q, use_container_width=True)
        with ch_g2:
            fig_26_s = px.bar(
                m26_ch, x='Month_Label', y='Sales_26', color='Channel', barmode='group',
                title="2026 VDF vs DF 分月供货销售额对比 ($)",
                color_discrete_map={'VDF': '#2563eb', 'DF': '#f97316'}
            )
            st.plotly_chart(fig_26_s, use_container_width=True)

    with h_tab2:
        ch_scope_26 = st.radio("选择 2026 明细视角：", ["VDF 与 DF 分开列示（同表按渠道排序）", "仅看 VDF 唯一 SKU", "仅看 DF 唯一 SKU"], horizontal=True)
        if ch_scope_26 == "仅看 VDF 唯一 SKU":
            show_26_tbl = vdf_26_rows
        elif ch_scope_26 == "仅看 DF 唯一 SKU":
            show_26_tbl = df_26_rows
        else:
            show_26_tbl = channel_unique_2026

        view_26_cols = ['Channel', 'SKU', 'Product_Name', 'Brand', 'Category', 'Owner', 'Qty_2026', 'Sales_2026', 'ASP_2026'] + MONTH_COLS_26
        if not show_26_tbl.empty:
            st.dataframe(
                show_26_tbl[view_26_cols].style.format({
                    'Qty_2026': '{:,.0f}',
                    'Sales_2026': '${:,.2f}', 'ASP_2026': '${:,.2f}',
                    **{c: '{:,.0f}' for c in MONTH_COLS_26}
                }),
                use_container_width=True,
                height=340
            )
        else:
            st.info("当前筛选条件下暂无数据。")

        buf_26 = BytesIO()
        with pd.ExcelWriter(buf_26, engine='openpyxl') as writer:
            if not vdf_26_rows.empty:
                vdf_26_rows[view_26_cols].to_excel(writer, sheet_name='2026_VDF唯一SKU月销', index=False)
            if not df_26_rows.empty:
                df_26_rows[view_26_cols].to_excel(writer, sheet_name='2026_DF唯一SKU月销', index=False)
            filtered_2026.to_excel(writer, sheet_name='2026_清洗后订单明细', index=False)

        st.download_button(
            label="📥 导出 2026 分渠道销售明细 (Excel)",
            data=buf_26.getvalue(),
            file_name="2026_HD_VDF_DF_Sales_Data.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    with h_tab3:
        st.dataframe(
            filtered_2026[['Channel', 'Date', 'SKU', 'Product_Name', 'Brand', 'Category', 'Owner', 'Qty', 'Unit_Price', 'Sales']]
            .sort_values('Date', ascending=False)
            .head(500)
            .style.format({
                'Date': lambda d: d.strftime('%Y-%m-%d') if pd.notna(d) else '',
                'Qty': '{:,.0f}',
                'Unit_Price': '${:,.2f}', 'Sales': '${:,.2f}'
            }),
            use_container_width=True,
            height=300
        )

# ---------------------------------------------------------
# 6. 构建【分渠道 + 唯一 SKU × 1-12月】2027 预估基准宽表
# ---------------------------------------------------------
master_df = channel_unique_2026.copy()
master_df['2027_预期供货价($)'] = (master_df['ASP_2026'] * (1 + init_price_adj / 100.0)).round(2)

for m in range(1, 13):
    col_26 = f"26_{m}月"
    col_27 = f"27_{m}月"
    master_df[col_27] = np.round(master_df[col_26] * (1 + init_growth / 100.0)).astype(int)

# ---------------------------------------------------------
# 7. 交互式单品 SKU 月度预估编辑区（DF / VDF 分开，组内 SKU 唯一）
# ---------------------------------------------------------
st.divider()
st.subheader("📝 第一步：DF 与 VDF 分渠道唯一 SKU 的 2027 年 1–12 月销量自定义编辑表")
st.markdown("""
- **唯一性保障**：VDF 内 `产品SKU` 唯一、DF 内 `发货产品` 唯一（每个渠道下同一 SKU 仅占一行）。
- **逐月自定义**：直接双击 **`27_1月` ~ `27_12月`** 单元格修改销量，或修改 **`27预期供货价($)`**。
- **新增新品**：拉到表格底部点 **`+`**，选择 `Channel`（`VDF` 或 `DF`）并填入该渠道内唯一的 `SKU` 与各月预估销量。
""")

edit_channel_filter = st.radio(
    "编辑表筛选视角：",
    ["全部（VDF 与 DF 分行展示）", "仅编辑 VDF（产品SKU 唯一）", "仅编辑 DF（发货产品 唯一）"],
    horizontal=True
)

base_for_edit = master_df.copy()
if edit_channel_filter == "仅编辑 VDF（产品SKU 唯一）":
    base_for_edit = base_for_edit[base_for_edit['Channel'] == 'VDF'].reset_index(drop=True)
elif edit_channel_filter == "仅编辑 DF（发货产品 唯一）":
    base_for_edit = base_for_edit[base_for_edit['Channel'] == 'DF'].reset_index(drop=True)

display_cols = ['Channel', 'SKU', 'Brand', 'Category', 'Owner', 'ASP_2026', '2027_预期供货价($)', 'Qty_2026']
if show_2026_months:
    display_cols += MONTH_COLS_26
display_cols += MONTH_COLS_27

col_cfg = {
    "Channel": st.column_config.SelectboxColumn("渠道", options=["VDF", "DF"], required=True, width="small"),
    "SKU": st.column_config.TextColumn("产品SKU(VDF)/发货SKU(DF)", required=True, width="medium"),
    "Brand": st.column_config.TextColumn("品牌", width="small"),
    "Category": st.column_config.TextColumn("产品属性", width="small"),
    "Owner": st.column_config.TextColumn("运营", width="small"),
    "ASP_2026": st.column_config.NumberColumn("26均价($)", format="$%.2f", disabled=True),
    "2027_预期供货价($)": st.column_config.NumberColumn("27预期供货价($)", min_value=0.0, step=1.0, format="$%.2f"),
    "Qty_2026": st.column_config.NumberColumn("26销量", format="%d", disabled=True),
}
for c26 in MONTH_COLS_26:
    col_cfg[c26] = st.column_config.NumberColumn(c26, format="%d", disabled=True)
for c27 in MONTH_COLS_27:
    col_cfg[c27] = st.column_config.NumberColumn(c27, min_value=0, step=1, format="%d")

edited_sku_df = st.data_editor(
    base_for_edit[display_cols],
    column_config=col_cfg,
    use_container_width=True,
    height=460,
    num_rows="dynamic",
    key="channel_unique_sku_editor"
)

# 强制执行：VDF 内 SKU 唯一、DF 内 SKU 唯一
clean_edited_df, dup_removed = enforce_unique_sku_per_channel(edited_sku_df)
if dup_removed > 0:
    st.warning(f"⚠️ 检测到同一渠道内有 {dup_removed} 条重复 SKU，系统已自动按【渠道 + 唯一 SKU】合并去重。")

# ---------------------------------------------------------
# 8. 实时重算 2027 指标（分 VDF / DF 及合计）
# ---------------------------------------------------------
calc_df = clean_edited_df.merge(
    master_df[['Channel', 'SKU', 'Sales_2026'] + [c for c in MONTH_COLS_26 if c not in clean_edited_df.columns]],
    on=['Channel', 'SKU'],
    how='left'
)
calc_df['Sales_2026'] = calc_df['Sales_2026'].fillna(0.0)
for c26 in MONTH_COLS_26:
    calc_df[c26] = calc_df[c26].fillna(0).astype(int)

calc_df['Qty_2027'] = calc_df[MONTH_COLS_27].sum(axis=1)
calc_df['Sales_2027'] = calc_df['Qty_2027'] * calc_df['2027_预期供货价($)']
calc_df['销量同比(%)'] = np.where(
    calc_df['Qty_2026'] > 0,
    ((calc_df['Qty_2027'] - calc_df['Qty_2026']) / calc_df['Qty_2026'] * 100).round(1),
    np.nan
)
calc_df['销售额同比(%)'] = np.where(
    calc_df['Sales_2026'] > 0,
    ((calc_df['Sales_2027'] - calc_df['Sales_2026']) / calc_df['Sales_2026'] * 100).round(1),
    np.nan
)

for m in range(1, 13):
    calc_df[f"27_{m}月_供货额($)"] = calc_df[f"27_{m}月"] * calc_df['2027_预期供货价($)']

vdf_27_rows = calc_df[calc_df['Channel'] == 'VDF']
df_27_rows = calc_df[calc_df['Channel'] == 'DF']

def calc_kpi_tuple(sub):
    q26 = sub['Qty_2026'].sum() if not sub.empty else 0
    q27 = sub['Qty_2027'].sum() if not sub.empty else 0
    s26 = sub['Sales_2026'].sum() if not sub.empty else 0.0
    s27 = sub['Sales_2027'].sum() if not sub.empty else 0.0
    qy = ((q27 - q26) / q26 * 100) if q26 > 0 else 0.0
    sy = ((s27 - s26) / s26 * 100) if s26 > 0 else 0.0
    asp27 = s27 / q27 if q27 > 0 else 0.0
    cnt = sub['SKU'].nunique() if not sub.empty else 0
    return q27, qy, s27, sy, asp27, cnt

vq27, vqy, vs27, vsy, vasp27, vcnt27 = calc_kpi_tuple(vdf_27_rows)
dq27, dqy, ds27, dsy, dasp27, dcnt27 = calc_kpi_tuple(df_27_rows)
tq27, tqy, ts27, tsy, tasp27, tcnt27 = calc_kpi_tuple(calc_df)

st.divider()
st.subheader("📊 第二步：2027 全年预估核心指标（VDF 与 DF 分开展示 + 合计）")

c_vdf1, c_vdf2, c_df1, c_df2, c_tot1, c_tot2 = st.columns(6)
c_vdf1.metric("VDF 2027 预估销量", f"{vq27:,.0f}", f"{vqy:+.1f}% vs 26")
c_vdf2.metric("VDF 2027 预估供货额", f"${vs27:,.0f}", f"{vsy:+.1f}% vs 26 ({vcnt27} SKU)")
c_df1.metric("DF 2027 预估销量", f"{dq27:,.0f}", f"{dqy:+.1f}% vs 26")
c_df2.metric("DF 2027 预估供货额", f"${ds27:,.0f}", f"{dsy:+.1f}% vs 26 ({dcnt27} SKU)")
c_tot1.metric("合计 2027 预估销量", f"{tq27:,.0f}", f"{tqy:+.1f}% vs 26")
c_tot2.metric("合计 2027 预估供货额", f"${ts27:,.0f}", f"{tsy:+.1f}% vs 26")

# ---------------------------------------------------------
# 9. 可视化对比与分渠道报表导出
# ---------------------------------------------------------
f_tab1, f_tab2, f_tab3 = st.tabs([
    "📈 VDF vs DF 分月销量与供货额对比",
    "🏆 VDF / DF 唯一 SKU 贡献排行",
    "📋 分渠道明细汇总表 & Excel 导出"
])

with f_tab1:
    m_labels = [f"{m}月" for m in range(1, 13)]
    vq27_m = [vdf_27_rows[f"27_{m}月"].sum() for m in range(1, 13)]
    dq27_m = [df_27_rows[f"27_{m}月"].sum() for m in range(1, 13)]
    vs27_m = [vdf_27_rows[f"27_{m}月_供货额($)"].sum() for m in range(1, 13)]
    ds27_m = [df_27_rows[f"27_{m}月_供货额($)"].sum() for m in range(1, 13)]

    p1, p2 = st.columns(2)
    with p1:
        fig_27q = go.Figure()
        fig_27q.add_trace(go.Bar(x=m_labels, y=vq27_m, name='VDF 2027预估销量', marker_color='#2563eb'))
        fig_27q.add_trace(go.Bar(x=m_labels, y=dq27_m, name='DF 2027预估销量', marker_color='#f97316'))
        fig_27q.update_layout(barmode='group', title="2027 VDF vs DF 月度预估销量 (台)", hovermode="x unified", height=380)
        st.plotly_chart(fig_27q, use_container_width=True)

    with p2:
        fig_27s = go.Figure()
        fig_27s.add_trace(go.Bar(x=m_labels, y=vs27_m, name='VDF 2027预估供货额($)', marker_color='#1d4ed8'))
        fig_27s.add_trace(go.Bar(x=m_labels, y=ds27_m, name='DF 2027预估供货额($)', marker_color='#ea580c'))
        fig_27s.update_layout(barmode='group', title="2027 VDF vs DF 月度预估供货销售额 ($)", hovermode="x unified", height=380)
        st.plotly_chart(fig_27s, use_container_width
