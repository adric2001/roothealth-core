import streamlit as st
import boto3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import time
import json
from pycognito import Cognito
from boto3.dynamodb.conditions import Key
from datetime import datetime
from dateutil.relativedelta import relativedelta

REGION = os.environ.get('AWS_REGION', 'us-east-1')
USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
CLIENT_ID = os.environ.get('COGNITO_CLIENT_ID', '')
TABLE_NAME = os.environ.get('DYNAMODB_TABLE', 'RootHealth_Stats')
SUPPLEMENTS_TABLE = "RootHealth_Supplements"
RELATIONSHIPS_TABLE = "RootHealth_Relationships"
BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', 'roothealth-raw-files-adric') 

st.set_page_config(page_title="RootHealth OS", page_icon="🧬", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .block-container { padding-top: 2rem; padding-bottom: 5rem; max_width: 1200px; }
    
    .metric-card {
        background-color: #1A1C24;
        border: 1px solid #2C2F3A;
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 12px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        transition: transform 0.2s;
    }
    .metric-card:hover { transform: translateY(-2px); border-color: #4CAF50; }
    
    .metric-label { color: #8F9BB3; font-size: 0.85rem; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
    .metric-value { color: #FFFFFF; font-size: 1.6rem; font-weight: 700; margin: 4px 0; }
    .metric-unit { color: #5F6B7C; font-size: 0.9rem; font-weight: 400; margin-left: 4px; }
    
    .delta-positive { color: #00E676; font-size: 0.8rem; font-weight: 600; background: rgba(0, 230, 118, 0.1); padding: 2px 6px; border-radius: 4px; }
    .delta-negative { color: #FF5252; font-size: 0.8rem; font-weight: 600; background: rgba(255, 82, 82, 0.1); padding: 2px 6px; border-radius: 4px; }
    .delta-neutral { color: #8F9BB3; font-size: 0.8rem; }

    @media (max-width: 600px) {
        .metric-value { font-size: 1.4rem; }
    }
</style>
""", unsafe_allow_html=True)

dynamodb = boto3.resource('dynamodb', region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
supp_table = dynamodb.Table(SUPPLEMENTS_TABLE)
rel_table = dynamodb.Table(RELATIONSHIPS_TABLE)
s3 = boto3.client('s3', region_name=REGION)
bedrock = boto3.client('bedrock-runtime', region_name=REGION)

def render_metric_card(label, value, unit, delta_val, delta_pct, time_str):
    if delta_val > 0:
        delta_html = f'<span class="delta-positive">▲ {delta_val:.1f} ({delta_pct:.0f}%)</span> <span class="delta-neutral">in {time_str}</span>'
    elif delta_val < 0:
        delta_html = f'<span class="delta-negative">▼ {abs(delta_val):.1f} ({abs(delta_pct):.0f}%)</span> <span class="delta-neutral">in {time_str}</span>'
    else:
        delta_html = '<span class="delta-neutral">No change</span>'
        
    if delta_val == 0 and time_str == "New":
        delta_html = '<span class="delta-neutral">✨ First Record</span>'

    html = f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">
            {value} <span class="metric-unit">{unit}</span>
        </div>
        <div>{delta_html}</div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

def get_time_diff(d1, d2):
    diff = relativedelta(d1, d2)
    if diff.years > 0: return f"{diff.years}y"
    if diff.months > 0: return f"{diff.months}mo"
    if diff.weeks > 0: return f"{diff.weeks}w"
    return f"{diff.days}d"

def save_user_preferences(metrics_list):
    try:
        table.put_item(Item={
            'user_id': st.session_state.username,
            'record_id': 'USER_SETTINGS', 
            'favorites': metrics_list,
            'upload_timestamp': str(int(time.time()))
        })
        return True
    except: return False

def get_user_preferences():
    try:
        response = table.get_item(Key={'user_id': st.session_state.username, 'record_id': 'USER_SETTINGS'})
        if 'Item' in response: return response['Item'].get('favorites', [])
    except: pass
    return ["Testosterone, Total", "Vitamin D", "Ferritin", "Body Weight"]

def save_user_profile(age, height, gender, goal, weight):
    try:
        table.put_item(Item={
            'user_id': st.session_state.username,
            'record_id': 'USER_PROFILE', 
            'age': age, 'height': height, 'gender': gender, 'goal': goal, 'weight': weight,
            'upload_timestamp': str(int(time.time()))
        })
        st.success("Saved!")
    except: st.error("Error saving profile")

def get_user_profile():
    try: return table.get_item(Key={'user_id': st.session_state.username, 'record_id': 'USER_PROFILE'}).get('Item', {})
    except: return {}

def update_manual_data(df_changes):
    for index, row in df_changes.iterrows():
        try:
            ts = str(int(row['Date'].timestamp())) if pd.notnull(row['Date']) else str(int(time.time()))
            rec_id = row.get('record_id')
            
            if not rec_id or pd.isna(rec_id):
                rec_id = f"{str(row['metric']).replace(' ', '_')}_{ts}"
            
            table.put_item(Item={
                'user_id': st.session_state.username,
                'record_id': rec_id,
                'metric': row['metric'],
                'value': str(row['value']),
                'unit': row['unit'],
                'upload_timestamp': ts,
                'source_file': 'Manual_Edit'
            })
        except Exception as e:
            st.error(f"Failed to update {row.get('metric')}: {e}")

def init_auth(username=None):
    if not USER_POOL_ID or not CLIENT_ID: st.stop()
    return Cognito(USER_POOL_ID, CLIENT_ID, username=username)

def login_user(username, password):
    try: u = init_auth(username); u.authenticate(password=password); return u
    except: return None

def register_user(email, password):
    try: u = init_auth(email); u.set_base_attributes(email=email); u.register(email, password); st.success("Check email for code.")
    except: st.error("Registration failed")

def confirm_user(email, code):
    try: u = init_auth(email); u.confirm_sign_up(code, username=email); st.success("Verified!")
    except: st.error("Verification failed")

def run_ai_coach(user_data, user_stack, user_profile):
    csv_data = user_data.to_csv(index=False)
    stack_txt = "\n".join([f"- {s['item_name']} ({s['dosage']} {s['frequency']})" for s in user_stack]) if user_stack else "None"
    
    prof_txt = f"Age: {user_profile.get('age','?')}\nGoal: {user_profile.get('goal','Health')}\nWeight: {user_profile.get('weight','?')}"
    
    prompt = f"Role: Elite Health Coach. Context: {prof_txt}. Labs: {csv_data}. Stack: {stack_txt}. Task: 1. Analysis 2. Stack Audit 3. Protocol. Tone: Direct."
    
    body = json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 2500, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]})
    try:
        r = bedrock.invoke_model(modelId="anthropic.claude-3-5-sonnet-20240620-v1:0", body=body)
        return json.loads(r['body'].read())['content'][0]['text']
    except Exception as e: return f"AI Error: {e}"

def get_data(uid):
    try: return [i for i in table.query(KeyConditionExpression=Key('user_id').eq(uid))['Items']]
    except: return []

if 'authenticated' not in st.session_state: st.session_state.authenticated = False
if 'username' not in st.session_state: st.session_state.username = None

if not st.session_state.authenticated:
    st.title("🧬 RootHealth")
    t1, t2, t3 = st.tabs(["Log In", "Sign Up", "Verify"])
    with t1:
        e, p = st.text_input("Email"), st.text_input("Password", type="password")
        if st.button("Log In"): 
            if login_user(e, p): st.session_state.authenticated = True; st.session_state.username = e; st.rerun()
    with t2:
        ne, np = st.text_input("New Email"), st.text_input("New Password", type="password")
        ic = st.text_input("Invite Code", type="password")
        if st.button("Join"): 
            if ic == os.environ.get("INVITE_CODE"): register_user(ne, np)
            else: st.error("Invalid Code")
    with t3:
        ve, vc = st.text_input("Verify Email"), st.text_input("Code")
        if st.button("Verify"): confirm_user(ve, vc)
    st.stop()

with st.sidebar:
    st.title("🧬 RootHealth")
    page = st.radio("Navigation", ["Dashboard", "Data Manager", "AI Coach", "Profile & Stack", "Coaching"], label_visibility="collapsed")
    
    st.markdown("---")
    with st.expander("📝 Quick Log Weight", expanded=False):
        with st.form("quick_log"):
            w = st.number_input("lbs", step=0.1, label_visibility="collapsed")
            if st.form_submit_button("Log"):
                ts = str(int(time.time()))
                table.put_item(Item={'user_id': st.session_state.username, 'record_id': f"Weight_{ts}", 'metric': "Body Weight", 'value': str(w), 'unit': 'lbs', 'upload_timestamp': ts})
                st.success("Saved"); time.sleep(0.5); st.rerun()
    
    if st.button("Log Out"): st.session_state.authenticated = False; st.rerun()

raw_data = get_data(st.session_state.username)
df = pd.DataFrame(raw_data)
if not df.empty:
    df['value'] = pd.to_numeric(df['value'], errors='coerce')
    df['Date'] = pd.to_datetime(pd.to_numeric(df['upload_timestamp'].fillna(0)), unit='s')
    df = df.dropna(subset=['value']).sort_values(by='Date')

if page == "Dashboard":
    st.header("Dashboard")
    if df.empty: 
        st.info("No data found. Go to 'Data Manager' to upload labs.")
    else:
        all_metrics = sorted(df['metric'].unique().tolist())
        saved_faves = get_user_preferences()
        current_faves = [m for m in saved_faves if m in all_metrics]
        
        with st.popover("⚙️ Customize Widgets"):
            new_faves = st.multiselect("Visible Metrics", all_metrics, default=current_faves)
            if st.button("Save Layout"):
                save_user_preferences(new_faves); st.rerun()

        if current_faves:
            cols = st.columns(3)
            for i, metric in enumerate(current_faves):
                m_df = df[df['metric'] == metric].sort_values(by='Date')
                if m_df.empty: continue
                
                curr = m_df.iloc[-1]
                val, unit = curr['value'], curr['unit']
                
                delta, pct, t_str = 0, 0, "New"
                if len(m_df) > 1:
                    prev = m_df.iloc[-2]
                    delta = val - prev['value']
                    pct = (delta / prev['value']) * 100 if prev['value'] != 0 else 0
                    t_str = get_time_diff(curr['Date'], prev['Date'])

                with cols[i % 3]:
                    render_metric_card(metric, val, unit, delta, pct, t_str)
        else:
            st.info("Select metrics in the ⚙️ menu to display them here.")

        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("Trends")
        sel = st.selectbox("Select Metric", all_metrics)
        
        chart_data = df[df['metric'] == sel]
        fig = px.line(chart_data, x="Date", y="value", markers=True)
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#8F9BB3", 
            xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="#2C2F3A"),
            height=300
        )
        fig.update_traces(line_color="#4CAF50", line_width=3, marker_size=8)
        st.plotly_chart(fig, use_container_width=True)

elif page == "Data Manager":
    st.header("Data Manager")
    
    t1, t2 = st.tabs(["📄 Upload Files", "✍️ Manual Editor"])
    
    with t1:
        st.write("Upload PDF or CSV reports from your lab.")
        files = st.file_uploader("Drag and drop", accept_multiple_files=True)
        if files and st.button("Process Files", type="primary"):
            for f in files: s3.put_object(Bucket=BUCKET_NAME, Key=f"uploads/{st.session_state.username}/{f.name}", Body=f.getvalue())
            st.success("Uploaded! AI is processing...")
            
    with t2:
        st.write("Edit incorrect values or add new data manually.")
        if df.empty:
            st.info("No data to edit. Add a row below.")
            edit_df = pd.DataFrame(columns=['metric', 'value', 'unit', 'Date', 'record_id'])
        else:
            edit_df = df[['metric', 'value', 'unit', 'Date', 'record_id']].copy()
            
        edited_df = st.data_editor(
            edit_df,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "metric": "Biomarker Name",
                "value": "Result",
                "unit": "Unit",
                "Date": st.column_config.DatetimeColumn("Date", format="D MMM YYYY"),
                "record_id": st.column_config.Column("ID (Hidden)", disabled=True) 
            },
            hide_index=True
        )
        
        if st.button("Save Manual Changes"):
            update_manual_data(edited_df)
            st.success("Database Updated!")
            time.sleep(1); st.rerun()

elif page == "AI Coach":
    st.header("Intelligence Center")
    
    c1, c2 = st.columns([1, 1])
    with c1:
        st.subheader("Correlations")
        if df.empty: st.warning("No data.")
        else:
            all_m = df['metric'].unique()
            m1 = st.selectbox("Left Axis", all_m, index=0)
            m2 = st.selectbox("Right Axis", all_m, index=1 if len(all_m)>1 else 0)
            d1 = df[df['metric'] == m1].sort_values('Date')
            d2 = df[df['metric'] == m2].sort_values('Date')
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=d1['Date'], y=d1['value'], name=m1, mode='lines+markers'))
            fig.add_trace(go.Scatter(x=d2['Date'], y=d2['value'], name=m2, mode='lines+markers', yaxis='y2'))
            fig.update_layout(yaxis=dict(title=m1), yaxis2=dict(title=m2, overlaying='y', side='right'), legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.subheader("AI Protocol Coach")
        if st.button("⚡ Run Full Audit", type="primary"):
            with st.spinner("Analyzing profile, labs, and stack..."):
                try: stack = supp_table.query(KeyConditionExpression=Key('user_id').eq(st.session_state.username)).get('Items', [])
                except: stack = []
                res = run_ai_coach(df, stack, get_user_profile())
                st.markdown(res)

elif page == "Profile & Stack":
    st.header("Profile & Stack")
    
    prof = get_user_profile()
    
    with st.container(border=True):
        st.subheader("Bio Profile")
        with st.form("prof_form"):
            c1, c2 = st.columns(2)
            age = c1.number_input("Age", value=int(prof.get('age', 25)), step=1)
            weight = c1.number_input("Current Weight (lbs)", value=float(prof.get('weight', 180)))
            gender = c1.selectbox("Gender", ["Male", "Female"], index=0 if prof.get('gender') == "Male" else 1)
            
            height = c2.text_input("Height", value=prof.get('height', ""))
            goal = c2.selectbox("Primary Goal", [
                "Optimization / Longevity", 
                "Muscle Gain / Hypertrophy", 
                "Fat Loss", 
                "Cognitive Performance", 
                "Libido / Hormone Health"
            ], index=0)
            
            if st.form_submit_button("Save Profile"):
                save_user_profile(age, height, gender, goal, weight)
                st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)
    
    with st.container(border=True):
        st.subheader("Supplement Stack")
        c1, c2 = st.columns([2, 1])
        with c2:
            st.caption("Add New Item")
            with st.form("stack_add"):
                n = st.text_input("Name")
                d = st.text_input("Dose")
                f = st.selectbox("Freq", ["Daily", "AM/PM", "Weekly", "EOD"])
                if st.form_submit_button("Add Item"):
                    supp_table.put_item(Item={'user_id': st.session_state.username, 'item_name': n, 'dosage': d, 'frequency': f})
                    st.rerun()
        with c1:
            try: 
                items = supp_table.query(KeyConditionExpression=Key('user_id').eq(st.session_state.username)).get('Items', [])
                if items: 
                    st.dataframe(pd.DataFrame(items)[['item_name', 'dosage', 'frequency']], use_container_width=True, hide_index=True)
                else:
                    st.info("Stack is empty.")
            except: pass

elif page == "Coaching":
    st.header("Coaching Portal")
    role = st.radio("I am a:", ["Client", "Coach"], horizontal=True)
    
    if role == "Client":
        st.write("Grant access to your coach so they can view your dashboard.")
        coach_em = st.text_input("Coach Email Address")
        if st.button("Link Coach"): 
            rel_table.put_item(Item={'coach_id': coach_em.lower(), 'client_id': st.session_state.username})
            st.success("Access Granted!")
            
    else:
        st.subheader("Client Roster")
        try:
            clients = rel_table.query(KeyConditionExpression=Key('coach_id').eq(st.session_state.username))['Items']
            if not clients: 
                st.info("No clients linked yet.")
            else:
                c_sel = st.selectbox("Select Client", [c['client_id'] for c in clients])
                if c_sel:
                    st.markdown(f"### Viewing: {c_sel}")
                    c_data = get_data(c_sel)
                    st.dataframe(pd.DataFrame(c_data))
        except: st.error("Error loading clients")