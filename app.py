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
# 2. 数据清洗与双渠道 SKU 对齐引擎
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
        
        # 提取 VDF 的 SKU 维度主数据（品牌、属性、品名、定位）
        meta_cols = [c for c in ['产品SKU', '品牌', '产品属性', '产品名称', '产品定位'] if c in vdf.columns]
        if '产品SKU' in meta_cols:
            meta_df = vdf[meta_cols].dropna(subset=['产品SKU']).copy()
            meta_df['产品SKU'] = meta_df['产品SKU'].astype(str).str.strip()
            meta_df = meta_df.drop_duplicates(subset=['产品SKU'])
            sku_meta_map = meta_df.set_index('产品SKU').to_dict('index')

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

    # --- 处理 DF 数据 ---
    if df_file is not None:
        df_raw = pd.read_excel(df_file) if df_file.name.endswith(('xlsx', 'xls')) else pd.read_csv(df_file)
        df_raw.columns = df_raw.columns.str.strip()
        
        skus = df_raw['产品SKU'].astype(str).str.strip()
        qty = pd.to_numeric(df_raw['下单数量'], errors='coerce').fillna(0)
        unit_p = pd.to_numeric(df_raw['折后供货价'], errors='coerce').fillna(0)
        
        raw_sales = pd.to_numeric(df_raw.get('总销售额', 0), errors='coerce').fillna(0)
        sales = np.where(raw_sales > 0, raw_sales, qty * unit_p)

        # 继承 VDF 中的品牌与属性，若 VDF 中无此 SKU 则标记为 DF专属
        brands = [sku_meta_map.get(s, {}).get('品牌', 'DF品牌(待归类)') for s in skus]
        cats = [sku_meta_map.get(s, {}).get('产品属性', 'DF通用属性') for s in skus]
        pos = [sku_meta_map.get(s, {}).get('产品定位', '常规款') for s in skus]
        names = [
            df_raw.get('产品描述', pd.Series(['']*len(skus))).iloc[i] 
            if pd.notna(df_raw.get('产品描述', pd.Series([None]*len(skus))).iloc[i]) 
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
    combined = combined[combined['SKU'] != 'nan']
    combined['Month'] = combined['Date'].dt.month
    return combined

def generate_demo_data():
    """生成单品 SKU 级别演示数据"""
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', '2026-12-31', freq='D')
    skus = [
        ('PD160B', 'Waykar 34 Pint 除湿机', 'Waykar', '除湿机', '主力爆款', 'VDF', 135.0, 'Kristen'),
        ('PD250B', 'Waykar 40 Pint 除湿机', 'Waykar', '除湿机', '利润款', 'DF', 168.0, 'Kristen'),
        ('PD250B', 'Waykar 40 Pint 除湿机', 'Waykar', '除湿机', '利润款', 'VDF', 165.0, 'Kristen'),
        ('KS-AC10', 'Kesnos 10000 BTU 移动空调', 'Kesnos', '移动空调', '季节款', 'VDF', 210.0, 'Zoey'),
        ('SR-MT12', 'Serweet 12寸混合床垫 Queen', 'Serweet', '床垫', '常青款', 'DF', 185.0, '陈菊'),
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
    vdf_uploaded = st.file_uploader("上传 2026 VDF 销售表 (xlsx/csv)", type=['xlsx', 'xls', 'csv'])
    df_uploaded = st.file_uploader("上传 2026 DF 销售表 (xlsx/csv)", type=['xlsx', 'xls', 'csv'])
    exclude_cancel = st.checkbox("VDF 自动剔除 Cancelled 订单", value=True)

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
    st.info("💡 当前处于**演示模式**。请在左侧上传 2026 年 VDF 与 DF 销售表，即可按单个 SKU 的 1-12 月实际销量进行自定义预估。")

if df_2026.empty:
    st.error("未能解析到有效数据，请检查上传文件表头。")
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
    sku_keyword = st.text_input("🔎 搜索特定 SKU 或产品名称", placeholder="输入 SKU 编码过滤，如 PD160B...")

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

# 1) 汇总每个 SKU 的 2026 全年销量、销售额与均价
sku_base = filtered_2026.groupby(sku_keys, as_index=False).agg(
    Product_Name=('Product_Name', 'first'),
    Positioning=('Positioning', 'first'),
    Qty_2026=('Qty', 'sum'),
    Sales_2026=('Sales', 'sum')
)
sku_base['ASP_2026'] = np.where(sku_base['Qty_2026'] > 0, (sku_base['Sales_2026'] / sku_base['Qty_2026']).round(2), 0.0)

# 2) 透视每个 SKU 在 2026 年 1-12 月的实际销量
pivot_26 = filtered_2026.pivot_table(
    index=sku_keys, columns='Month', values='Qty', aggfunc='sum', fill_value=0
)
for m in range(1, 13):
    if m not in pivot_26.columns:
        pivot_26[m] = 0
pivot_26 = pivot_26[list(range(1, 13))]
pivot_26.columns = MONTH_COLS_26
pivot_26 = pivot_26.reset_index()

# 合并基础信息与 2026 月度销量
master_df = sku_base.merge(pivot_26, on=sku_keys, how='left')

# 3) 生成 2027 初始预估列（可被用户逐单元格覆盖）
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
- **直接修改月度销量**：双击任意 SKU 的 **`27_1月` ~ `27_12月`** 单元格输入你的目标台数（支持从 Excel 复制一列/一行直接粘贴）。
- **新增 2027 新品 SKU**：拉到表格最底部点击 **`+`** 行，手动填写 `Channel`、`SKU`、`Brand`、`Category`、`2027_预期供货价($)` 及各月预估销量即可。
""")

# 决定编辑表展示哪些列
display_cols = ['Channel', 'SKU', 'Brand', 'Category', 'Owner', 'ASP_2026', '2027_预期供货价($)', 'Qty_2026']
if show_2026_months:
    display_cols += MONTH_COLS_26
display_cols +=
