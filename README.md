# Waykar FY2027 Sales Forecast Workbench

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy with GitHub + Streamlit Community Cloud
1. Create a GitHub repository.
2. Upload `app.py` and `requirements.txt`.
3. In Streamlit Community Cloud, connect the repository.
4. Set Main file path to `app.py`, then deploy.

## Included
- Separate DF and VDF uploads (Excel/CSV)
- Column mapping
- 2026 historical analysis by channel and SKU
- SKU growth and ASP assumptions
- New SKU creation with manual annual unit/ASP targets
- Monthly allocation by historical seasonality, equal split, or custom weights
- Editable monthly SKU unit targets
- Monthly sales calculation and Excel export

## Important
VDF `Total Cost` and DF `总销售额` may not be equivalent revenue definitions. DF `下单数量` may represent replenishment PO quantity to DFC rather than consumer POS sell-through. Validate these definitions before treating the combined totals as company GMV.
