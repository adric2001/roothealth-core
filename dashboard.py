import streamlit as st
import boto3
import pandas as pd
import plotly.express as px
import os

st.set_page_config(page_title="RootHealth Command Center", page_icon="🧬", layout="wide")

TABLE_NAME = os.environ.get('DYNAMODB_TABLE', 'RootHealth_Stats')
REGION = os.environ.get('AWS_REGION', 'us-east-1') 

dynamodb = boto3.resource('dynamodb', region_name=REGION)
table = dynamodb.Table(TABLE_NAME)

def get_data():
    """Fetch all data from DynamoDB"""
    try:
        response = table.scan()
        items = response.get('Items', [])
        return items
    except Exception as e:
        st.error(f"Error connecting to DynamoDB: {e}")
        return []

st.sidebar.title("🧬 RootHealth")
st.sidebar.markdown("System Status: **Online** 🟢")
user_filter = st.sidebar.text_input("User ID Filter", value="adric2001")

st.title("Biometric Optimization Dashboard")

with st.spinner('Fetching biometric data from cloud...'):
    raw_data = get_data()

if not raw_data:
    st.warning("No data found in DynamoDB. Upload a CSV to S3 first!")
    st.stop()

df = pd.DataFrame(raw_data)

if 'upload_timestamp' not in df.columns:
    df['upload_timestamp'] = 0 

df['upload_timestamp'] = df['upload_timestamp'].fillna(0)

df = df[df['user_id'] == user_filter]

df['upload_timestamp'] = pd.to_numeric(df['upload_timestamp'])
df['Date'] = pd.to_datetime(df['upload_timestamp'], unit='s')
df['value'] = pd.to_numeric(df['value'])

st.subheader("Key Biomarkers (Latest)")
if not df.empty:
    latest_date = df['Date'].max()
    latest_data = df[df['Date'] == latest_date]

    col1, col2, col3, col4 = st.columns(4)

    def get_metric_latest(metric_name):
        row = latest_data[latest_data['metric'] == metric_name]
        if not row.empty:
            return row.iloc[0]['value'], row.iloc[0]['unit']
        return None, None

    test_val, test_unit = get_metric_latest("Testosterone")
    if test_val:
        col1.metric("Testosterone", f"{test_val} {test_unit}")

    vit_val, vit_unit = get_metric_latest("Vitamin D")
    if vit_val:
        col2.metric("Vitamin D", f"{vit_val} {vit_unit}")

    fer_val, fer_unit = get_metric_latest("Ferritin")
    if fer_val:
        col3.metric("Ferritin", f"{fer_val} {fer_unit}")

st.markdown("---")
st.subheader("Trends Over Time")

unique_metrics = df['metric'].unique().tolist()
selected_metric = st.selectbox("Select Biomarker to Analyze", unique_metrics)

chart_data = df[df['metric'] == selected_metric].sort_values(by="Date")

if not chart_data.empty:
    fig = px.line(chart_data, x="Date", y="value", title=f"{selected_metric} History", markers=True)
    
    try:
        low = float(chart_data.iloc[0]['range_low'])
        high = float(chart_data.iloc[0]['range_high'])
        fig.add_hrect(y0=low, y1=high, line_width=0, fillcolor="green", opacity=0.1, annotation_text="Optimal Range")
    except:
        pass 
        
    st.plotly_chart(fig, use_container_width=True)

with st.expander("View Raw Database Records"):
    st.dataframe(df)