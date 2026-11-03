import sys
# Принудительно отключаем конфликтующие системные прокси-настройки платформы
import os
os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
os.environ["STREAMLIT_SERVER_PORT"] = "8501"
import os
import json
import time
import io
import streamlit as st
from groq import Groq

# Библиотеки для чтения сторонних форматов книг
from pypdf import PdfReader
import docx
from ebooklib import epub
import ebooklib
from bs4 import BeautifulSoup

# =====================================
# НАСТРОЙКИ ОФИЦИАЛЬНОГО ВЕБ-САЙТА
# =====================================
# Файл истории будет создаваться в директории скрипта
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

# Инициализация облачного клиента Groq
# Ожидает переменную окружения GROQ_API_KEY на сервере/хостинге
try:
    client = Groq()
except Exception:
    client = None

# Используем модель Llama 3.3 70B (актуальный флагман Groq для сложных задач перевода)
MODEL_NAME = "llama-3.3-70b-versatile"

# Оптимальный размер чанка для Groq — 4000 символов (обеспечивает удержание контекста сцены)
CHUNK_SIZE = 4000

# =====================================
# ЛОГИКА СЛУЖЕБНЫХ ФУНКЦИЙ (ИСТОРИЯ)
# =====================================
def load_history():
    """Загружает историю выполненных переводов с диска."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as file:
                return json.load(file)
        except (json.JSONDecodeError, OSError):
            return []
    return []

def save_history(history_data):
    """Сохраняет историю выполненных переводов на диск."""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as file:
            json.dump(history_data, file, ensure_ascii=False, indent=4)
    except OSError as e:
        print(f"[BookFlow] Ошибка сохранения истории: {e}")

def split_text(text, max_size=CHUNK_SIZE):
    """Разбивает текст по абзацам с контролем максимальной длины чанка."""
    text = text.replace("\r", "").replace("***", "\n---")
    paragraphs = text.split("\n")
    chunks = []
    current_chunk = []
    current_length = 0
    
    for paragraph in paragraphs:
        if not paragraph.strip():
            continue
        if current_length + len(paragraph) + 1 > max_size:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
            current_chunk = [paragraph]
            current_length = len(paragraph)
        else:
            current_chunk.append(paragraph)
            current_length += len(paragraph) + 1
            
    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks

# =====================================
# ИЗВЛЕЧЕНИЕ ТЕКСТА ИЗ ЗАГРУЖЕННЫХ ФАЙЛОВ
# =====================================
def extract_text_from_file(uploaded_file):
    """Парсит файлы различных форматов и возвращает чистый текст."""
    filename = uploaded_file.name.lower()
    file_bytes = uploaded_file.read()
    
    if filename.endswith('.txt'):
        return file_bytes.decode("utf-8", errors="ignore")
        
    elif filename.endswith('.docx'):
        doc = docx.Document(io.BytesIO(file_bytes))
        return "\n".join([para.text for para in doc.paragraphs])
        
    elif filename.endswith('.pdf'):
        reader = PdfReader(io.BytesIO(file_bytes))
        full_text = []
        for page in reader.pages:
            text = page.extract_text()
            if text: 
                full_text.append(text)
        return "\n".join(full_text)
        
    elif filename.endswith('.epub'):
        book = epub.read_epub(io.BytesIO(file_bytes))
        full_text = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                full_text.append(soup.get_text())
        return "\n".join(full_text)
        
    elif filename.endswith('.fb2'):
        soup = BeautifulSoup(file_bytes, 'xml')
        return "\n".join([p.get_text() for p in soup.find_all('p')])
        
    return ""

# =====================================
# СБОРКА И ГЕНЕРАЦИЯ ФАЙЛОВ КНИГ
# =====================================
def generate_epub(title, text):
    """Формирует валидный электронный файл книги EPUB в оперативной памяти."""
    book = epub.EpubBook()
    book.set_identifier('bookflow_official_internal_id')
    book.set_title(title)
    book.set_language('ru')
    
    html_content = f"<h1>{title}</h1>"
    for para in text.split("\n"):
        if para.strip():
            html_content += f"<p>{para.strip()}</p>"
            
    c1 = epub.EpubHtml(title='Глава 1', file_name='chap_1.xhtml', lang='ru')
    c1.content = html_content
    book.add_item(c1)
    
    book.toc = (epub.Link('chap_1.xhtml', 'Начало', 'chap_1'),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav', c1]
    
    out = io.BytesIO()
    epub.write_epub(out, book)
    return out.getvalue()

def generate_fb2(title, text):
    """Формирует структурированную XML-схему формата FB2 в байты."""
    fb2_paragraphs = ""
    for para in text.split("\n"):
        if para.strip():
            fb2_paragraphs += f"<p>{para.strip()}</p>"
            
    fb2_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://gribuser.ru" xmlns:l="http://w3.org">
  <description>
    <title-info>
      <genre>fiction</genre>
      <author><first-name>BookFlow</first-name><last-name>AI</last-name></author>
      <book-title>{title}</book-title>
      <lang>ru</lang>
      <src-lang>en</src-lang>
    </title-info>
  </description>
  <body>
    <section>
      <title><p>{title}</p></title>
      {fb2_paragraphs}
    </section>
  </body>
</FictionBook>"""
    return fb2_xml.encode('utf-8')

# =====================================
# ВЗАИМОДЕЙСТВИЕ С ОБЛАЧНЫМ API GROQ
# =====================================
def translate_text_groq(text, glossary=None):
    """Осуществляет перевод чанков через API Groq Cloud с поддержкой глоссария."""
    if not client:
        raise ValueError("Ключ API Groq не обнаружен. Настройте переменную окружения GROQ_API_KEY.")

    chunks = split_text(text)
    translated_chunks = []
    
    glossary_context = ""
    if glossary and glossary.strip():
        glossary_context = (
            f"Используй следующие точные переводы для персонажей и терминов в этой книге:\n"
            f"{glossary.strip()}\n"
            "Не переводи эти имена дословно, строго следуй указанному глоссарию.\n"
        )
        
    # Инициализация динамических элементов прогресса в интерфейсе Streamlit
    progress_bar = st.progress(0)
    status_text = st.empty()
    total_chunks = len(chunks)
        
    for number, chunk in enumerate(chunks, start=1):
        status_text.text(f"⏳ Groq Cloud переводит фрагмент {number} из {total_chunks}...")
        
        system_prompt = (
            "Ты — профессиональный литературный переводчик книг и художественной прозы с английского на русский. "
            "Твоя задача — переводить предоставленный текст фрагмента. "
            "Сохраняй оригинальный художественный стиль, живые диалоги, атмосферу повествования и игру слов. "
            f"{glossary_context}"
            "Отвечай СТРОГО готовым качественным переводом на русском языке. "
            "Не добавляй абсолютно никаких вводных фраз от себя, примечаний переводчика, сносок или комментариев."
        )
        
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': f"Переведи этот фрагмент:\n\n{chunk}"}
                    ],
                    temperature=0.2
                )
                translated_text = response.choices.message.content.strip()
                if not translated_text: 
                    raise ValueError("Получен пустой ответ от API")
                
                translated_chunks.append(translated_text)
                break
            except Exception as error:
                print(f"[BookFlow] Сбой API на попытке {attempt + 1}: {error}")
                if attempt < 2: 
                    time.sleep(2)  # Пауза для обхода возможных ограничений частоты запросов (Rate Limits)
                else: 
                    raise
        
        # Обновление индикатора прогресса на веб-странице
        progress_bar.progress(number / total_chunks)
                
    status_text.text("✅ Перевод успешно завершен!")
    return "\n\n".join(translated_chunks)

# =====================================
# ИНТЕРФЕЙС ПРИЛОЖЕНИЯ STREAMLIT
# =====================================
# Конфигурация страницы
st.set_page_config(page_title="BookFlow — Официальный сайт", page_icon="📚", layout="wide")

# Кастомные стили заголовков
st.markdown("<h1 style='text-align: center; margin-bottom: 0;'>📚 BookFlow AI</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #888888; font-size: 1.1rem;'>Официальная веб-платформа моментального литературного перевода книг</p>", unsafe_allow_html=True)
st.markdown("---")

# Инициализация состояний сессии пользователя
if "history" not in st.session_state: 
    st.session_state["history"] = load_history()
if "translation" not in st.session_state: 
    st.session_state["translation"] = ""

# Разделение интерфейса сайта на две функциональные зоны (Колонки)
col_input, col_result = st.columns(2, gap="large")

# --- КОЛОНКА СЛЕВА: ВВОД ДАННЫХ И НАСТРОЙКИ ---
with col_input:
    st.subheader("1. Загрузка исходных материалов")
    uploaded_file = st.file_uploader(
        "Выберите файл книги на вашем устройстве:", 
        type=["txt", "pdf", "docx", "epub", "fb2"]
    )
    
    # Автоматическое определение названия книги по имени файла
    clean_title = st.text_input(
        "Название книги (для сохранения файлов):", 
        value=uploaded_file.name if uploaded_file else ""
    )
    if "." in clean_title:
        clean_title = ".".join(clean_title.split(".")[:-1])
        
    file_text = ""
    if uploaded_file is not None:
        with st.spinner("Анализ структуры и извлечение текста из книги..."):
            file_text = extract_text_from_file(uploaded_file)
        if file_text: 
            st.success(f"Текст успешно подготовлен! Всего символов: {len(file_text)}")
        else: 
            st.error("Не удалось прочитать структуру загруженного файла.")
            
    manual_text = st.text_area("Или вставьте текстовый фрагмент вручную:")
    
    st.subheader("2. Персонализация контекста (Глоссарий)")
    glossary_input = st.text_area(
        "Укажите правила перевода имён, фамилий и ключевых терминов фэндома:", 
        placeholder="Пример записи:\nTony Stark -> Тони Старк\nLadybug -> Леди Баг\nWinterfell -> Винтерфелл",
        height=120
    )
    
    # Кнопка триггера перевода
    if st.button("🚀 Запустить облачный перевод книги", type="primary", use_container_width=True):
        text_to_translate = file_text if uploaded_file is not None else manual_text
        if not text_to_translate.strip():
            st.warning("Отсутствует material для обработки. Загрузите файл или добавьте текст вручную.")
        else:
            try:
                translation = translate_text_groq(text_to_translate, glossary=glossary_input)
                if translation:
                    st.session_state["translation"] = translation
                    # Добавляем результат в историю сессии и сохраняем локально
                    st.session_state["history"].append({
                        "title": clean_title.strip() or "Переведенное произведение",
                        "translation": translation
                    })
                    save_history(st.session_state["history"])
            except Exception as error:
                st.error(f"Произошла ошибка при обработке книги сервером: {error}")

# --- КОЛОНКА СПРАВА: ВЫВОД РЕЗУЛЬТАТОВ И СКАЧИВАНИЕ ---
with col_result:
    st.subheader("3. Результат и экспорт перевода")
    
    if st.session_state["translation"]:
        file_base_name = clean_title.strip() or "translated_book"
        
        st.write("📥 Выберите формат сохранения готовой книги:")
        
        # Сетка кнопок скачивания в 4 колонки
        c1, c2, c3, c4 = st.columns(4)
        
        with c1:
            st.download_button(
                label="📄 .TXT", 
                data=st.session_state["translation"], 
                file_name=f"{file_base_name}.txt", 
                mime="text/plain", 
                use_container_width=True
            )
            
        with c2:
            try:
                doc_file = docx.Document()
                doc_file.add_heading(file_base_name, level=1)
                for para in st.session_state["translation"].split("\n"):
                    if para.strip(): 
                        doc_file.add_paragraph(para.strip())
                bio = io.BytesIO()
                doc_file.save(bio)
                st.download_button(
                    label="📝 .DOCX", 
                    data=bio.getvalue(), 
                    file_name=f"{file_base_name}.docx", 
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", 
                    use_container_width=True
                )
            except Exception as e: 
                st.error(f"Ошибка экспорта в Word: {e}")
            
        with c3:
            try:
                epub_bytes = generate_epub(file_base_name, st.session_state["translation"])
                st.download_button(
                    label="📕 .EPUB", 
                    data=epub_bytes, 
                    file_name=f"{file_base_name}.epub", 
                    mime="application/epub+zip", 
                    use_container_width=True
                )
            except Exception as e: 
                st.error(f"Ошибка сборки EPUB: {e}")
            
        with c4:
            try:
                fb2_bytes = generate_fb2(file_base_name, st.session_state["translation"])
                st.download_button(
                    label="📗 .FB2", 
                    data=fb2_bytes, 
                    file_name=f"{file_base_name}.fb2", 
                    mime="application/x-fictionbook+xml", 
                    use_container_width=True
                )
            except Exception as e: 
                st.error(f"Ошибка сборки FB2: {e}")
            
        st.markdown("---")
        # Полноэкранный скроллящийся блок предпросмотра
        st.text_area(
            "Интерактивное окно предпросмотра перевода:", 
            value=st.session_state["translation"], 
            height=430
        )
    else:
        st.info("Здесь отобразится готовый художественный перевод книги и кнопки скачивания форматов после запуска процесса.")

# =====================================
# ПОДВАЛ САЙТА: АРХИВ / ИСТОРИЯ
# =====================================
if st.session_state["history"]:
    st.markdown("---")
    st.subheader("📚 Архив выполненных переводов (Локальная история):")
    
    # Итерируемся по истории в обратном порядке (последние переводы сверху)
    for idx, item in enumerate(reversed(st.session_state["history"])):
        hist_title = item.get('title', 'Без названия')
        hist_text = item.get('translation', '')
        
        with st.expander(f"📖 Книга: «{hist_title}» (Сохранение #{idx+1})"):
            if hist_text:
                st.caption("📥 Быстрый повторный экспорт файла из архива:")
                
                hc1, hc2, hc3, hc4 = st.columns(4)
                file_base_name = hist_title.strip() or f"archived_book_{idx+1}"
                
                with hc1:
                    st.download_button(
                        label="Скачать .TXT", 
                        data=hist_text, 
                        file_name=f"{file_base_name}.txt", 
                        mime="text/plain", 
                        key=f"h_txt_{idx}"
                    )
                with hc2:
                    try:
                        doc_file = docx.Document()
                        doc_file.add_heading(file_base_name, level=1)
                        for para in hist_text.split("\n"):
                            if para.strip(): doc_file.add_paragraph(para.strip())
                        bio = io.BytesIO()
                        doc_file.save(bio)
                        st.download_button(
                            label="Скачать .DOCX", 
                            data=bio.getvalue(), 
                            file_name=f"{file_base_name}.docx", 
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", 
                            key=f"h_docx_{idx}"
                        )
                    except Exception as e: st.error(f"Ошибка DOCX: {e}")
                with hc3:
                    try:
                        epub_bytes = generate_epub(file_base_name, hist_text)
                        st.download_button(
                            label="Скачать .EPUB", 
                            data=epub_bytes, 
                            file_name=f"{file_base_name}.epub", 
                            mime="application/epub+zip", 
                            key=f"h_epub_{idx}"
                        )
                    except Exception as e: st.error(f"Ошибка EPUB: {e}")
                with hc4:
                    try:
                        fb2_bytes = generate_fb2(file_base_name, hist_text)
                        st.download_button(
                            label="Скачать .FB2", 
                            data=fb2_bytes, 
                            file_name=f"{file_base_name}.fb2", 
                            mime="application/x-fictionbook+xml", 
                            key=f"h_fb2_{idx}"
                        )
                    except Exception as e: st.error(f"Ошибка FB2: {e}")
                
                st.markdown("---")
                st.text_area("Архивный текст:", value=hist_text, height=250, key=f"h_area_{idx}")
