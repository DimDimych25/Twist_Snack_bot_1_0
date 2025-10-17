import logging
import pandas as pd
import io
import os
import asyncio
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from math import radians, sin, cos, sqrt, atan2
from aiohttp import web
import threading
import time

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "8106167716:AAEl3--rXh86H7z8SxwoFKOuS5CerJ5vW_U"
GOOGLE_SHEETS_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSWt19kiS7cdliNwfs9SriPW-LGrr4lmLl2Q6AojRqGyqwy9lI91PB-9OYKi5LOJBbbB5dx6uaqA5tK/pub?gid=0&single=true&output=csv"
MAX_DISTANCE_KM = 10  # Максимальное расстояние для поиска точек

# Получаем порт из переменной окружения (нужно для Render)
PORT = int(os.environ.get('PORT', 8080))

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


# ===== ФУНКЦИИ ДЛЯ РАБОТЫ С ДАННЫМИ =====

def haversine(lat1, lon1, lat2, lon2):
    """Расчет расстояния между двумя точками на Земле (в км)"""
    R = 6371  # Радиус Земли в км

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


def load_data_from_google_sheets():
    """Загрузка данных из Google Sheets"""
    try:
        df = pd.read_csv(GOOGLE_SHEETS_CSV_URL)
        # Убедимся, что координаты числовые
        df['Широта'] = pd.to_numeric(df['Широта'], errors='coerce')
        df['Долгота'] = pd.to_numeric(df['Долгота'], errors='coerce')
        df = df.dropna(subset=['Широта', 'Долгота'])
        return df
    except Exception as e:
        logging.error(f"Ошибка загрузки данных: {e}")
        return pd.DataFrame()


def get_unique_categories(df):
    """Получение уникальных типов мест"""
    return df['Тип'].unique().tolist()


def get_subcategories_by_type(df, selected_type):
    """Получение подтипов для выбранного типа"""
    return df[df['Тип'] == selected_type]['Подтип'].unique().tolist()


def find_nearest_places(df, user_lat, user_lon, place_type, subcategory, max_distance=MAX_DISTANCE_KM, n=5):
    """Поиск n ближайших мест по фильтрам в радиусе max_distance км"""
    # Фильтрация по типу и подтипу
    filtered_df = df[(df['Тип'] == place_type) & (df['Подтип'] == subcategory)]

    if filtered_df.empty:
        return []

    # Расчет расстояний и фильтрация по максимальному расстоянию
    distances = []
    for _, row in filtered_df.iterrows():
        distance = haversine(user_lat, user_lon, row['Широта'], row['Долгота'])
        if distance <= max_distance:  # Фильтруем только точки в радиусе max_distance км
            distances.append((row, distance))

    if not distances:
        return []

    # Сортировка по расстоянию и выбор топ-n
    distances.sort(key=lambda x: x[1])
    return distances[:n]


def generate_yandex_maps_link(lat, lon):
    """Генерация ссылки на Яндекс.Карты"""
    return f"https://yandex.ru/maps/?pt={lon},{lat}&z=17&l=map"


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показ главного меню (используется в нескольких местах)"""
    keyboard = [
        [KeyboardButton("🗺️ План поездки")],
        [KeyboardButton("🔍 Поиск места")],
        [KeyboardButton("🏠 Главное меню")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    # Проверяем, является ли update сообщением или callback_query
    if update.message:
        await update.message.reply_text(
            "🏠 Главное меню:\n\nВыберите режим работы:",
            reply_markup=reply_markup
        )
    else:  # Это callback_query
        # Для callback_query отправляем новое сообщение вместо редактирования старого
        await update.callback_query.message.reply_text(
            "🏠 Главное меню:\n\nВыберите режим работы:",
            reply_markup=reply_markup
        )


# ===== ОСНОВНЫЕ ФУНКЦИИ БОТА =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await show_main_menu(update, context)


async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик главного меню"""
    text = update.message.text

    if text == "🗺️ План поездки":
        await send_trip_plan(update, context)
    elif text == "🔍 Поиск места":
        await request_location(update, context)
    elif text == "🏠 Главное меню":
        await show_main_menu(update, context)


async def send_trip_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка плана поездки (CSV файл)"""
    df = load_data_from_google_sheets()

    if df.empty:
        await update.message.reply_text("❌ В данный момент база данных недоступна. Попробуйте позже.")
        return

    # Создаем CSV файл в памяти
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    csv_buffer.seek(0)

    # Отправляем файл пользователю
    await update.message.reply_document(
        document=io.BytesIO(csv_buffer.getvalue().encode()),
        filename="food_guide_plan.csv",
        caption="📋 Вот ваш план поездки! Вы можете импортировать этот CSV файл в различные картографические сервисы."
    )


async def request_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос местоположения пользователя"""
    keyboard = [
        [KeyboardButton("📍 Отправить местоположение", request_location=True)],
        [KeyboardButton("🏠 Главное меню")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        f"📍 Для поиска ближайших мест (в радиусе {MAX_DISTANCE_KM} км), пожалуйста, поделитесь вашим местоположением:",
        reply_markup=reply_markup
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка полученного местоположения"""
    location = update.message.location
    context.user_data['user_lat'] = location.latitude
    context.user_data['user_lon'] = location.longitude

    df = load_data_from_google_sheets()
    categories = get_unique_categories(df)

    if not categories:
        await update.message.reply_text("❌ В базе данных нет доступных категорий.")
        return

    # Создаем клавиатуру с типами мест
    keyboard = []
    for category in categories:
        keyboard.append([InlineKeyboardButton(category, callback_data=f"type_{category}")])

    # Добавляем кнопку возврата в главное меню
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"📍 Местоположение получено! Будем искать места в радиусе {MAX_DISTANCE_KM} км.\n\nТеперь выберите тип места:",
        reply_markup=reply_markup
    )


async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора категории"""
    query = update.callback_query
    await query.answer()

    selected_type = query.data.replace("type_", "")
    context.user_data['selected_type'] = selected_type

    df = load_data_from_google_sheets()
    subcategories = get_subcategories_by_type(df, selected_type)

    if not subcategories:
        await query.edit_message_text("❌ Для выбранного типа нет доступных подкатегорий.")
        return

    # Создаем клавиатуру с подтипами
    keyboard = []
    for subcategory in subcategories:
        keyboard.append([InlineKeyboardButton(subcategory, callback_data=f"subtype_{subcategory}")])

    # Добавляем кнопку возврата в главное меню
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"🏷️ Выбран тип: {selected_type}\nТеперь выберите подкатегорию:",
        reply_markup=reply_markup
    )


async def handle_subcategory_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора подкатегории и поиск ближайших мест"""
    query = update.callback_query
    await query.answer()

    selected_subtype = query.data.replace("subtype_", "")
    selected_type = context.user_data.get('selected_type')
    user_lat = context.user_data.get('user_lat')
    user_lon = context.user_data.get('user_lon')

    if not all([selected_type, user_lat, user_lon]):
        await query.edit_message_text("❌ Ошибка: данные не найдены. Начните поиск заново.")
        return

    df = load_data_from_google_sheets()
    nearest_places = find_nearest_places(df, user_lat, user_lon, selected_type, selected_subtype)

    if not nearest_places:
        await query.edit_message_text(
            f"❌ К сожалению, поблизости (в радиусе {MAX_DISTANCE_KM} км) не найдено мест по вашим критериям.\n\n"
            f"Попробуйте изменить критерии поиска или выберите другую категорию."
        )
        return

    # Сохраняем результаты в user_data для последующего выбора
    context.user_data['nearest_places'] = [
        (place.to_dict(), distance) for place, distance in nearest_places
    ]

    # Формируем сообщение со списком мест
    message_text = f"🏆 Найденные места ({selected_type} - {selected_subtype}) в радиусе {MAX_DISTANCE_KM} км:\n\n"
    keyboard = []

    for i, (place_data, distance) in enumerate(nearest_places, 1):
        message_text += f"{i}. {place_data['Название']} (расстояние: {distance:.1f} км)\n"
        if 'Примечание' in place_data and pd.notna(place_data['Примечание']):
            message_text += f"   📝 {place_data['Примечание']}\n"
        message_text += "\n"

        keyboard.append([InlineKeyboardButton(
            f"{i}. {place_data['Название']}",
            callback_data=f"place_{i - 1}"
        )])

    # Добавляем кнопку возврата в главное меню
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message_text + "Выберите место для построения маршрута:",
        reply_markup=reply_markup
    )


async def handle_place_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора конкретного места"""
    query = update.callback_query
    await query.answer()

    place_index = int(query.data.replace("place_", ""))
    nearest_places = context.user_data.get('nearest_places', [])

    if place_index >= len(nearest_places):
        await query.edit_message_text("❌ Ошибка: место не найдено.")
        return

    place_data, distance = nearest_places[place_index]
    yandex_maps_url = generate_yandex_maps_link(place_data['Широта'], place_data['Долгота'])

    message_text = (
        f"📍 {place_data['Название']}\n"
        f"📌 Адрес: {place_data['Адрес']}\n"
        f"📏 Расстояние: {distance:.1f} км\n"
        f"🏷️ Тип: {place_data['Тип']} - {place_data['Подтип']}\n"
    )

    if 'Примечание' in place_data and pd.notna(place_data['Примечание']):
        message_text += f"📝 Примечание: {place_data['Примечание']}\n"

    # Кнопка для возврата в главное меню
    keyboard = [[InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        message_text,
        reply_markup=reply_markup
    )

    # Отправляем отдельное сообщение со ссылкой
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"🗺️ [Построить маршрут в Яндекс.Картах]({yandex_maps_url})",
        parse_mode='Markdown'
    )


async def handle_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка возврата в главное меню из инлайн-кнопки"""
    query = update.callback_query
    await query.answer()

    # Просто отправляем новое сообщение с главным меню вместо редактирования старого
    keyboard = [
        [KeyboardButton("🗺️ План поездки")],
        [KeyboardButton("🔍 Поиск места")],
        [KeyboardButton("🏠 Главное меню")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await query.message.reply_text(
        "🏠 Главное меню:\n\nВыберите режим работы:",
        reply_markup=reply_markup
    )


async def handle_unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка неизвестных сообщений"""
    await update.message.reply_text("🤔 Я не понимаю эту команду. Используйте кнопки меню.")


# ===== ВЕБ-СЕРВЕР ДЛЯ RENDER =====

async def health_check(request):
    """Обработчик для health check запросов"""
    return web.Response(text="Bot is running")


def run_web_server():
    """Запуск веб-сервера в отдельном потоке"""

    async def create_app():
        app = web.Application()
        app.router.add_get('/health', health_check)
        app.router.add_get('/', health_check)
        return app

    async def run_app():
        app = await create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        logging.info(f"Web server started on port {PORT}")
        # Бесконечный цикл чтобы сервер не завершался
        while True:
            await asyncio.sleep(3600)  # Sleep for 1 hour

    # Запускаем в отдельном event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_app())
    except Exception as e:
        logging.error(f"Web server error: {e}")
    finally:
        loop.close()


def start_web_server_thread():
    """Запуск веб-сервера в отдельном потоке"""
    thread = threading.Thread(target=run_web_server, daemon=True)
    thread.start()
    logging.info("Web server thread started")
    return thread


# ===== ЗАПУСК БОТА =====

def setup_bot_handlers(application):
    """Настройка обработчиков бота"""
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))

    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # Обработчики callback-запросов (инлайн кнопки)
    application.add_handler(CallbackQueryHandler(handle_category_selection, pattern="^type_"))
    application.add_handler(CallbackQueryHandler(handle_subcategory_selection, pattern="^subtype_"))
    application.add_handler(CallbackQueryHandler(handle_place_selection, pattern="^place_"))
    application.add_handler(CallbackQueryHandler(handle_main_menu_callback, pattern="^main_menu$"))

    # Обработчик неизвестных сообщений
    application.add_handler(MessageHandler(filters.ALL, handle_unknown_message))


def main():
    """Основная функция запуска бота"""
    # Создаем и настраиваем приложение бота
    application = Application.builder().token(BOT_TOKEN).build()

    # Настраиваем обработчики
    setup_bot_handlers(application)

    # Запускаем веб-сервер в отдельном потоке
    start_web_server_thread()

    # Даем время веб-серверу запуститься
    time.sleep(2)

    # Запуск бота
    logging.info("Бот запущен...")

    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True  # Важно: отбрасываем ожидающие обновления при запуске
        )
    except Exception as e:
        logging.error(f"Bot error: {e}")
    finally:
        logging.info("Bot stopped")


if __name__ == "__main__":
    main()