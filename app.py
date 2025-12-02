import os
import sqlite3
import hashlib
from datetime import datetime

import streamlit as st
from openai import OpenAI
from pypdf import PdfReader
import docx
import base64

# ------------- CONFIG & CLIENT ------------- #
st.set_page_config(
    page_title="AI Quiz & Cheat Sheet Generator",
    page_icon="📚",
    layout="wide"
)

OPENAI_API_KEY= os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    st.warning("⚠️ OPENAI_API_KEY not set. Set it as an environment variable.")
client = OpenAI(api_key=OPENAI_API_KEY)


# ------------- DATABASE SETUP ------------- #
DB_PATH = "app.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT,
            isbn TEXT,
            chapter_label TEXT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    conn.commit()
    conn.close()


init_db()


# ------------- AUTH HELPERS ------------- #
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def create_user(email: str, password: str):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (email, hash_password(password), datetime.utcnow().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Email already registered."
    conn.close()
    return True, "User created successfully."


def authenticate_user(email: str, password: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM users WHERE email = ? AND password_hash = ?",
        (email, hash_password(password)),
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


# ------------- FILE PARSERS ------------- #
def extract_text_from_pdf(uploaded_file):
    reader = PdfReader(uploaded_file)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
    return text


def extract_text_from_docx(uploaded_file):
    doc = docx.Document(uploaded_file)
    return "\n".join([para.text for para in doc.paragraphs])


# ------------- PRINT BUTTON ------------- #
def make_print_button(html_content: str, label: str = "🖨️ Print"):
    b64 = base64.b64encode(html_content.encode()).decode()
    href = f"""
    <a href="data:text/html;base64,{b64}" target="_blank"
       style="padding:10px 20px; background:#4CAF50; color:white; 
       border-radius:8px; text-decoration:none; font-size:16px;">
       {label}
    </a>
    """
    return href


# ------------- OPENAI HELPERS ------------- #
def generate_quiz_from_text(
    text: str,
    chapter_label: str,
    question_type: str,
    difficulty: str,
    num_mcq: int,
    num_subjective: int,
    num_tf: int,
):
    instructions = []

    if question_type in ["MCQ", "Mixed"]:
        instructions.append(
            f"- {num_mcq} multiple-choice questions with 1 correct answer and 3 plausible distractors."
        )
    if question_type in ["Subjective", "Mixed"]:
        instructions.append(
            f"- {num_subjective} subjective (short-answer / open-ended) questions."
        )
    if question_type in ["TF", "Mixed"]:
        instructions.append(
            f"- {num_tf} True/False questions with the correct answer indicated."
        )

    requirements = "\n".join(instructions)

    prompt = f"""
You are an expert teacher.

Create quiz questions ONLY from the text below.
Chapter: {chapter_label}
Difficulty: {difficulty}

TEXT:
\"\"\"{text[:15000]}\"\"\"

Requirements:
{requirements}

Format them clearly with headings:

## Multiple Choice
Q1. ...
A. ...
B. ...
C. ...
D. ...
Correct: B

## Subjective
Q1. ...
Answer (for teacher only): ...

## True/False
Q1. Statement...
Answer: True
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def generate_cheat_sheet(text: str, chapter_label: str, difficulty: str):
    prompt = f"""
You are an expert educator.

Summarize the chapter into a cheat sheet for students.

Chapter: {chapter_label}
Level: {difficulty}

Use the text below and create:

- A short overview (2–3 sentences)
- 5–15 bullet points of the MOST important ideas
- Definitions of key terms (if present)
- Optional: small example or analogy where helpful

Keep it student-friendly and concise.

TEXT:
\"\"\"{text[:15000]}\"\"\"
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.4,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


# ------------- CHAPTER STORAGE ------------- #
def save_chapter(user_id: int, title: str, isbn: str, chapter_label: str, content: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chapters (user_id, title, isbn, chapter_label, content, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, title, isbn, chapter_label, content, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_user_chapters(user_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title, isbn, chapter_label, created_at FROM chapters WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_chapter_content(chapter_id: int, user_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM chapters WHERE id = ? AND user_id = ?",
        (chapter_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


# ------------- SESSION STATE SETUP ------------- #
if "user" not in st.session_state:
    st.session_state.user = None


# ------------- AUTH UI ------------- #
def show_auth_page():
    st.title("📚 Teacher Login – AI Quiz & Cheat Sheet App")

    tab_login, tab_signup = st.tabs(["Login", "Sign up"])

    with tab_login:
        st.subheader("Login")
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        if st.button("Login"):
            user = authenticate_user(email, password)
            if user:
                st.session_state.user = user
                st.success("Logged in successfully.")
                st.rerun()
            else:
                st.error("Invalid email or password.")

    with tab_signup:
        st.subheader("Create a new account")
        email = st.text_input("Email", key="signup_email")
        password = st.text_input("Password", type="password", key="signup_password")
        password2 = st.text_input(
            "Confirm Password", type="password", key="signup_password2"
        )

        if st.button("Sign up"):
            if not email or not password:
                st.error("Email and password are required.")
            elif password != password2:
                st.error("Passwords do not match.")
            else:
                ok, msg = create_user(email, password)
                if ok:
                    st.success(msg + " You can log in now.")
                else:
                    st.error(msg)


# ------------- MAIN APP PAGES ------------- #
def show_chapter_page(user):
    st.header("📘 Chapters – Add & Manage")

    with st.expander("➕ Add new chapter", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            title = st.text_input("Book Title (optional)")
            isbn = st.text_input("ISBN (optional)")
        with col2:
            chapter_label = st.text_input("Chapter (e.g., 'Chapter 3 – Derivatives')")

        uploaded_file = st.file_uploader(
            "Upload chapter file (PDF / DOCX / TXT)", type=["pdf", "docx", "txt"]
        )
        paste_text = st.text_area(
            "Or paste chapter content", height=200, placeholder="Paste chapter text here..."
        )

        if st.button("Save chapter"):
            content = ""
            if uploaded_file:
                if uploaded_file.type == "application/pdf":
                    content = extract_text_from_pdf(uploaded_file)
                elif (
                    uploaded_file.type
                    == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ):
                    content = extract_text_from_docx(uploaded_file)
                else:
                    content = uploaded_file.read().decode("utf-8")
            else:
                content = paste_text

            if not content.strip():
                st.error("Please upload or paste some chapter content.")
            else:
                save_chapter(user["id"], title, isbn, chapter_label, content)
                st.success("Chapter saved successfully.")
                st.rerun()

    st.subheader("📂 Your saved chapters")
    chapters = get_user_chapters(user["id"])
    if not chapters:
        st.info("No chapters saved yet.")
    else:
        for ch in chapters:
            st.markdown(
                f"- **{ch['title'] or 'Untitled'}** | {ch['chapter_label'] or ''} | "
                f"ISBN: {ch['isbn'] or 'N/A'} | Saved: {ch['created_at'][:19]}"
            )


def show_quiz_page(user):
    st.header("📝 Generate Quiz")

    chapters = get_user_chapters(user["id"])
    if not chapters:
        st.info("You have no chapters yet. Go to *Chapters* and add one first.")
        return

    chapter_options = {f"{c['title'] or 'Untitled'} – {c['chapter_label'] or ''}": c["id"] for c in chapters}
    selected_label = st.selectbox("Select a chapter", list(chapter_options.keys()))
    chapter_id = chapter_options[selected_label]
    chapter = get_chapter_content(chapter_id, user["id"])

    st.markdown(f"**Selected chapter:** {chapter['title'] or 'Untitled'} – {chapter['chapter_label'] or ''}")

    col1, col2 = st.columns(2)
    with col1:
        question_type = st.selectbox(
            "Question type",
            ["Mixed", "MCQ", "Subjective", "TF"],
            index=0,
        )
        difficulty = st.selectbox(
            "Difficulty",
            ["Easy", "Medium", "Hard"],
            index=1,
        )

    with col2:
        num_mcq = st.number_input("MCQs", 0, 50, 5)
        num_subjective = st.number_input("Subjective questions", 0, 50, 3)
        num_tf = st.number_input("True/False", 0, 50, 2)

    if st.button("Generate quiz"):
        with st.spinner("Generating quiz with AI..."):
            quiz_text = generate_quiz_from_text(
                text=chapter["content"],
                chapter_label=chapter["chapter_label"] or "",
                question_type=question_type,
                difficulty=difficulty,
                num_mcq=num_mcq,
                num_subjective=num_subjective,
                num_tf=num_tf,
            )

        st.success("Quiz generated.")
        st.markdown("### 📄 Quiz (teacher view)")
        st.markdown(quiz_text)

        st.download_button(
            "⬇️ Download quiz as .txt",
            quiz_text,
            file_name="quiz.txt",
            mime="text/plain",
        )

        # Printable HTML version
        html_print = f"""
        <html>
        <head>
        <title>Quiz – {chapter['chapter_label']}</title>
        <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1, h2 {{ text-align: center; }}
        </style>
        </head>
        <body>
        <h1>Quiz</h1>
        <h2>{chapter['title'] or ''} – {chapter['chapter_label'] or ''}</h2>
        <div>{quiz_text.replace("\n", "<br>")}</div>
        </body>
        </html>
        """
        st.markdown(make_print_button(html_print, "🖨️ Print quiz"), unsafe_allow_html=True)


def show_cheat_sheet_page(user):
    st.header("📌 Cheat Sheet / Summary")

    chapters = get_user_chapters(user["id"])
    if not chapters:
        st.info("You have no chapters yet. Go to *Chapters* and add one first.")
        return

    chapter_options = {f"{c['title'] or 'Untitled'} – {c['chapter_label'] or ''}": c["id"] for c in chapters}
    selected_label = st.selectbox("Select a chapter", list(chapter_options.keys()))
    chapter_id = chapter_options[selected_label]
    chapter = get_chapter_content(chapter_id, user["id"])

    difficulty = st.selectbox(
        "Student level",
        ["Middle School", "High School", "Undergraduate", "Graduate"],
        index=2,
    )

    if st.button("Generate cheat sheet"):
        with st.spinner("Generating cheat sheet with AI..."):
            cheat_text = generate_cheat_sheet(
                text=chapter["content"],
                chapter_label=chapter["chapter_label"] or "",
                difficulty=difficulty,
            )

        st.success("Cheat sheet generated.")
        st.markdown("### 📄 Cheat Sheet (student handout)")
        st.markdown(cheat_text)

        st.download_button(
            "⬇️ Download cheat sheet as .txt",
            cheat_text,
            file_name="cheat_sheet.txt",
            mime="text/plain",
        )

        html_print = f"""
        <html>
        <head>
        <title>Cheat Sheet – {chapter['chapter_label']}</title>
        <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1, h2 {{ text-align: center; }}
        </style>
        </head>
        <body>
        <h1>Cheat Sheet</h1>
        <h2>{chapter['title'] or ''} – {chapter['chapter_label'] or ''}</h2>
        <div>{cheat_text.replace("\n", "<br>")}</div>
        </body>
        </html>
        """
        st.markdown(make_print_button(html_print, "🖨️ Print cheat sheet"), unsafe_allow_html=True)


# ------------- ROUTER ------------- #
def main():
    user = st.session_state.user

    if not user:
        show_auth_page()
        return

    # Logged-in layout
    st.sidebar.write(f"👤 Logged in as: **{user['email']}**")
    if st.sidebar.button("Logout"):
        st.session_state.user = None
        st.rerun()

    page = st.sidebar.radio(
        "Navigation",
        ["Chapters", "Generate Quiz", "Cheat Sheet"],
    )

    if page == "Chapters":
        show_chapter_page(user)
    elif page == "Generate Quiz":
        show_quiz_page(user)
    elif page == "Cheat Sheet":
        show_cheat_sheet_page(user)


if __name__ == "__main__":
    main()
