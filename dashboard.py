import streamlit as st
import boto3
import pandas as pd
import plotly.express as px
import os
import time 
from pycognito import Cognito
from boto3.dynamodb.conditions import Key

REGION = os.environ.get('AWS_REGION', 'us-east-1')
USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
CLIENT_ID = os.environ.get('COGNITO_CLIENT_ID', '')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE', 'RootHealth_Stats')
SUPPLEMENTS_TABLE = "RootHealth_Supplements" 
BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'roothealth-raw-files-adric') 

st.set_page_config(page_title="RootHealth", page_icon="🧬", layout="wide")

def init_auth(username=None):
    """Initialize Cognito connection with optional username"""
    if not USER_POOL_ID or not CLIENT_ID:
        st.error("⚠️ Auth Configuration Missing! Check environment variables.")
        st.stop()
    return Cognito(USER_POOL_ID, CLIENT_ID, username=username)

def login_user(username, password):
    try:
        u = init_auth(username)
        u.authenticate(password=password)
        return u
    except Exception as e:
        st.error(f"Login Failed: {e}")
        return None

def register_user(email, password):
    try:
        u = init_auth(email)
        u.set_base_attributes(email=email)
        u.register(email, password)
        st.success("Account created! Check your email for the confirmation code.")
    except Exception as e:
        st.error(f"Registration Failed: {e}")

def confirm_user(email, code):
    try:
        u = init_auth(email)
        u.confirm_sign_up(code, username=email)
        st.success("Account verified! You can now log in.")
    except Exception as e:
        st.error(f"Verification Failed: {e}")

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'username' not in st.session_state:
    st.session_state.username = None

if not st.session_state.authenticated:
    st.title("🧬 RootHealth Access (Beta)")
    
    tab1, tab2, tab3 = st.tabs(["Log In", "Sign Up", "Verify Account"])
    
    with tab1:
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.button("Log In"):
            if email and password:
                user = login_user(email, password)
                if user:
                    st.session_state.authenticated = True
                    st.session_state.username = email
                    st.rerun()
            else:
                st.warning("Please enter email and password")

    with tab2:
        new_email = st.text_input("New Email")
        new_pass = st.text_input("New Password", type="password")
        if st.button("Create Account"):
            register_user(new_email, new_pass)

    with tab3:
        v_email = st.text_input("Email to Verify")
        code = st.text_input("Verification Code (from email)")
        if st.button("Verify"):
            confirm_user(v_email, code)
            
    st.stop() 

dynamodb = boto3.resource('dynamodb', region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
supp_table = dynamodb.Table(SUPPLEMENTS_TABLE)
s3 = boto3.client('s3', region_name=REGION)

st.sidebar.title("🧬 RootHealth")
st.sidebar.write(f"User: **{st.session_state.username}**")
if st.sidebar.button("Log Out"):
    st.session_state.authenticated = False
    st.session_state.username = None
    st.rerun()

tab_overview, tab_upload, tab_stack = st.tabs(["📊 Dashboard", "📤 Upload Data", "💊 My Stack"])

with tab_overview:
    st.header("Biometric Optimization Dashboard")

    def get_data(user_id):
        try:
            response = table.scan()
            items = response.get('Items', [])
            return [i for i in items if i['user_id'] == user_id]
        except Exception as e:
            st.error(f"Database Error: {e}")
            return []

    with st.spinner('Syncing data...'):
        raw_data = get_data(st.session_state.username)

    if not raw_data:
        st.info("👋 Welcome! You have no biometric data yet. Go to the 'Upload Data' tab to get started.")
    else:
        df = pd.DataFrame(raw_data)

        if 'upload_timestamp' not in df.columns:
            df['upload_timestamp'] = 0 
        df['upload_timestamp'] = df['upload_timestamp'].fillna(0)
        df['upload_timestamp'] = pd.to_numeric(df['upload_timestamp'])
        df['Date'] = pd.to_datetime(df['upload_timestamp'], unit='s')
        df['value'] = pd.to_numeric(df['value'], errors='coerce')

        df = df.dropna(subset=['value'])

        st.subheader("Key Biomarkers (Latest)")
        if not df.empty:
            latest_date = df['Date'].max()
            latest_data = df[df['Date'] == latest_date]

            c1, c2, c3 = st.columns(3)

            def get_metric_latest(metric_name):
                row = latest_data[latest_data['metric'] == metric_name]
                if not row.empty:
                    return row.iloc[0]['value'], row.iloc[0]['unit']
                return None, None

            test_val, test_unit = get_metric_latest("Testosterone")
            if test_val: c1.metric("Testosterone", f"{test_val} {test_unit}")

            vit_val, vit_unit = get_metric_latest("Vitamin D")
            if vit_val: c2.metric("Vitamin D", f"{vit_val} {vit_unit}")

            fer_val, fer_unit = get_metric_latest("Ferritin")
            if fer_val: c3.metric("Ferritin", f"{fer_val} {fer_unit}")

        st.markdown("---")
        st.subheader("Trends Over Time")
        unique_metrics = df['metric'].unique().tolist()
        if unique_metrics:
            selected_metric = st.selectbox("Select Biomarker to Analyze", unique_metrics)
            chart_data = df[df['metric'] == selected_metric].sort_values(by="Date")

            if not chart_data.empty:
                fig = px.line(chart_data, x="Date", y="value", title=f"{selected_metric} History", markers=True)
                
                try:
                    low = float(chart_data.iloc[0]['range_low'])
                    high = float(chart_data.iloc[0]['range_high'])
                    fig.add_hrect(y0=low, y1=high, line_width=0, fillcolor="green", opacity=0.1, annotation_text="Optimal")
                except:
                    pass 
                st.plotly_chart(fig, use_container_width=True)
        
        with st.expander("View Raw Database Records"):
            st.dataframe(df)

with tab_upload:
    st.header("Upload Lab Results")
    st.write("Upload your blood work CSV or PDF here. We will process it and auto-refresh when done.")
    
    uploaded_file = st.file_uploader("Choose a file", type=["csv", "pdf"])

    if uploaded_file is not None:
        if st.button("Process File"):
            try:
                file_path = f"uploads/{st.session_state.username}/{uploaded_file.name}"
                
                with st.spinner("Uploading to secure cloud storage..."):
                    s3.put_object(
                        Bucket=BUCKET_NAME, 
                        Key=file_path, 
                        Body=uploaded_file.getvalue()
                    )

                progress_text = "Analysis in progress. This typically takes 10-20 seconds..."
                my_bar = st.progress(0, text=progress_text)
                
                max_retries = 15 
                success = False
                
                for i in range(max_retries):
                    current_progress = int((i / max_retries) * 90)
                    my_bar.progress(current_progress, text=f"Processing... ({i*2}s)")
                    
                    time.sleep(2) 
                    
                    try:
                        response = table.query(
                            KeyConditionExpression=Key('user_id').eq(st.session_state.username)
                        )
                        items = response.get('Items', [])
                        
                        new_data_found = any(item.get('source_file') == file_path for item in items)
                        
                        if new_data_found:
                            my_bar.progress(100, text="Processing Complete!")
                            success = True
                            break
                    except Exception as e:
                        print(f"Polling error: {e}") 
                
                if success:
                    st.success("✅ Data processed successfully! Refreshing dashboard...")
                    time.sleep(1)
                    st.rerun() 
                else:
                    my_bar.empty()
                    st.warning("⚠️ Processing is taking longer than usual. The data will appear shortly. You can manually refresh later.")

            except Exception as e:
                st.error(f"Upload failed: {e}")

with tab_stack:
    st.header("Current Protocol")
    st.write("Track your supplements and medications.")
    
    with st.form("add_supp_form"):
        c1, c2, c3 = st.columns(3)
        new_item = c1.text_input("Name (e.g. Vitamin D)")
        new_dose = c2.text_input("Dosage (e.g. 5000 IU)")
        new_freq = c3.selectbox("Frequency", ["Daily", "EOD", "Weekly", "As Needed"])
        submitted = st.form_submit_button("Add to Stack")
        
        if submitted and new_item:
            try:
                supp_table.put_item(
                    Item={
                        'user_id': st.session_state.username,
                        'item_name': new_item,
                        'dosage': new_dose,
                        'frequency': new_freq
                    }
                )
                st.success(f"Added {new_item} to stack.")
                st.rerun()
            except Exception as e:
                st.error(f"Error saving item: {e}")

    st.markdown("---")
    try:
        response = supp_table.query(
            KeyConditionExpression=Key('user_id').eq(st.session_state.username)
        )
        items = response.get('Items', [])
        
        if items:
            stack_df = pd.DataFrame(items)
            st.table(stack_df[['item_name', 'dosage', 'frequency']])
            
            if st.button("⚡ Run Interaction Check"):
                st.info("AI Analysis Engine coming in Phase 3 update...")
        else:
            st.info("Your stack is empty. Add items above.")
            
    except Exception as e:
        if "ResourceNotFoundException" in str(e):
            st.warning("Supplements table not found. Please run 'terraform apply'.")
        else:
            st.error(f"Error fetching stack: {e}")