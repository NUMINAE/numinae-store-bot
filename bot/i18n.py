"""English/Russian strings for everything customer-facing. Admin-facing
strings stay in English and stay where they're already defined — this module
holds only what a customer sees.

t(key, lang, **kwargs) looks up STRINGS[key][lang], falls back to English on
a missing key or a missing language, and logs a WARNING rather than raising —
a missing translation must never break a screen. kwargs are applied with
str.format(); any value that is user- or database-sourced must already be
escaped via render.esc() before being passed in, exactly as it would be
formatted into an f-string directly.

Product/category/variant names and descriptions, wallet addresses, network
names, transaction hashes, order numbers, and the settings values the owner
types (welcome_text, shipping_note, support_note) are all data, not UI copy —
none of it lives here, and none of it is translated.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

LANGUAGES = ("en", "ru")
DEFAULT_LANGUAGE = "en"

STRINGS: dict[str, dict[str, str]] = {
    # -- language picker --------------------------------------------------
    "lang.picker": {
        "en": "Please choose your language / Пожалуйста, выберите язык:",
        "ru": "Please choose your language / Пожалуйста, выберите язык:",
    },
    "lang.button_en": {"en": "English", "ru": "English"},
    "lang.button_ru": {"en": "Русский", "ru": "Русский"},
    "lang.invalid": {"en": "Invalid language.", "ru": "Invalid language."},
    # -- common: main menu, nudges, shared chrome --------------------------
    "common.welcome_default": {
        "en": "Welcome to the Persian Tea Shop! Use the menu below to get started.",
        "ru": "Добро пожаловать в Persian Tea Shop! Используйте меню ниже, чтобы начать.",
    },
    "common.browse_teas_button": {"en": "Browse teas", "ru": "Выбрать чай"},
    "common.my_cart_button": {"en": "My cart", "ru": "Моя корзина"},
    "common.my_cart_count": {"en": "My cart ({count})", "ru": "Моя корзина ({count})"},
    "common.my_orders_button": {"en": "My orders", "ru": "Мои заказы"},
    "common.help_button": {"en": "Help", "ru": "Помощь"},
    "common.language_button": {"en": "Language / Язык", "ru": "Language / Язык"},
    "common.back_to_menu_button": {"en": "Back to menu", "ru": "Назад в меню"},
    "common.reply_keyboard_intro": {
        "en": "Use the buttons below to browse, view your cart, or check your orders.",
        "ru": "Используйте кнопки ниже, чтобы посмотреть каталог, корзину или свои заказы.",
    },
    "common.menu_restored": {"en": "Menu restored.", "ru": "Меню восстановлено."},
    "common.type_below": {"en": "Type below.", "ru": "Напишите сообщение ниже."},
    "common.nudge": {
        "en": "I'm not sure what to do with that. Here's the menu:\n\n{menu}",
        "ru": "Не совсем понял. Вот меню:\n\n{menu}",
    },
    "common.expired_menu": {"en": "This menu has expired.", "ru": "Это меню больше не активно."},
    "common.help_text": {
        "en": (
            "After selecting your items and submitting your general location, choose "
            "your payment method to view the payment address. Once paid, send the "
            "transaction hash or a payment screenshot. After confirmation, your order "
            "will be dispatched within two hours, and the handoff area — agreed with "
            "you beforehand — will be confirmed for collection."
        ),
        "ru": (
            "После выбора товаров и указания вашего примерного местоположения выберите "
            "способ оплаты, чтобы увидеть адрес для платежа. После оплаты отправьте хэш "
            "транзакции или скриншот платежа. После подтверждения ваш заказ будет "
            "отправлен в течение двух часов, а место передачи — согласованное с вами "
            "заранее — будет подтверждено для получения."
        ),
    },
    # -- catalogue.py -------------------------------------------------------
    "catalogue.no_stock": {
        "en": "Nothing in stock right now — check back soon!",
        "ru": "Сейчас ничего нет в наличии — загляните позже!",
    },
    "catalogue.our_teas_heading": {"en": "Our teas", "ru": "Наш чай"},
    "catalogue.choose_category": {"en": "Choose a category:", "ru": "Выберите категорию:"},
    "catalogue.tap_to_view": {
        "en": "Tap a tea to view details.",
        "ru": "Нажмите на чай, чтобы посмотреть подробности.",
    },
    "catalogue.price_from": {"en": " — from {price}", "ru": " — от {price}"},
    "catalogue.no_weights": {
        "en": "No weights are currently available for this tea.",
        "ru": "Для этого чая сейчас нет доступных вариантов веса.",
    },
    "catalogue.back_to_products": {"en": "Back to products", "ru": "Назад к товарам"},
    "catalogue.back_to_categories": {"en": "Back to categories", "ru": "Назад к категориям"},
    "catalogue.weight_label": {"en": "Weight: {label}", "ru": "Вес: {label}"},
    "catalogue.unit_price_label": {"en": "Unit price: {price}", "ru": "Цена за единицу: {price}"},
    "catalogue.total_for_qty": {
        "en": "Total for {qty}: {total}",
        "ru": "Итого за {qty} шт.: {total}",
    },
    "catalogue.min_qty_alert": {
        "en": "Minimum quantity is {min}.",
        "ru": "Минимальное количество — {min}.",
    },
    "catalogue.max_qty_alert": {
        "en": "Maximum quantity is {max}.",
        "ru": "Максимальное количество — {max}.",
    },
    "catalogue.add_to_cart_button": {"en": "Add to cart", "ru": "Добавить в корзину"},
    "catalogue.back_to_product": {"en": "Back to product", "ru": "Назад к товару"},
    "catalogue.item_unavailable_alert": {
        "en": "This item is no longer available.",
        "ru": "Этот товар больше недоступен.",
    },
    "catalogue.added_to_cart": {
        "en": "Added {qty} × {product} {variant} to your cart.",
        "ru": "Добавлено в корзину: {product} {variant} × {qty}.",
    },
    "catalogue.keep_browsing_button": {"en": "Keep browsing", "ru": "Продолжить покупки"},
    "catalogue.view_cart_button": {"en": "View cart", "ru": "Посмотреть корзину"},
    # -- cart.py --------------------------------------------------------------
    "cart.empty": {"en": "Your cart is empty.", "ru": "Ваша корзина пуста."},
    "cart.heading": {"en": "Your cart", "ru": "Ваша корзина"},
    "cart.line_unavailable_suffix": {"en": " (no longer available)", "ru": " (больше недоступно)"},
    "cart.remove_button": {"en": "✕ Remove {name}", "ru": "✕ Удалить {name}"},
    "cart.subtotal": {"en": "Subtotal: {amount}", "ru": "Промежуточный итог: {amount}"},
    "cart.checkout_button": {"en": "Checkout", "ru": "Оформить заказ"},
    "cart.clear_cart_button": {"en": "Clear cart", "ru": "Очистить корзину"},
    "cart.clear_confirm": {
        "en": "Clear all {count} {noun} from your cart?",
        "ru": "Удалить все товары из корзины ({count} шт.)?",
    },
    "cart.item_singular": {"en": "item", "ru": "товар"},
    "cart.item_plural": {"en": "items", "ru": "товаров"},
    "cart.yes_clear_button": {"en": "Yes, clear", "ru": "Да, очистить"},
    "cart.no_button": {"en": "No", "ru": "Нет"},
    # -- checkout.py: summary / order status labels --------------------------
    "checkout.summary_heading": {"en": "Order summary", "ru": "Ваш заказ"},
    "checkout.line_item": {
        "en": "{name} — {qty} × {price} = {total}",
        "ru": "{name} — {qty} × {price} = {total}",
    },
    "checkout.total": {"en": "Total: {amount}", "ru": "Итого: {amount}"},
    "checkout.continue_button": {"en": "Continue", "ru": "Продолжить"},
    "checkout.back_to_cart_button": {"en": "Back to cart", "ru": "Назад в корзину"},
    "checkout.payments_unavailable": {
        "en": "Payments are temporarily unavailable — please try again shortly.",
        "ru": "Оплата временно недоступна — пожалуйста, попробуйте немного позже.",
    },
    "checkout.location_prompt": {
        "en": "Please enter your general location or region.",
        "ru": "Пожалуйста, укажите ваше примерное местоположение или регион.",
    },
    "checkout.use_previous_location_button": {
        "en": "Use my previous location",
        "ru": "Использовать предыдущее местоположение",
    },
    "checkout.no_previous_location_alert": {
        "en": "No previous location on file.",
        "ru": "Предыдущее местоположение не найдено.",
    },
    "checkout.location_confirm_heading": {
        "en": "Please confirm your location:",
        "ru": "Пожалуйста, подтвердите местоположение:",
    },
    "checkout.confirm_button": {"en": "Confirm", "ru": "Подтвердить"},
    "checkout.reenter_button": {"en": "Re-enter", "ru": "Ввести заново"},
    "checkout.location_too_long": {
        "en": "That's too long (max {max} characters). Please shorten it and send it again.",
        "ru": "Слишком длинно (максимум {max} символов). Пожалуйста, сократите и отправьте снова.",
    },
    "checkout.location_too_short": {
        "en": (
            "That looks a bit short — please give enough detail to identify your "
            "general location or region, for example a city and district, on at "
            "least two lines:\n\n{template}"
        ),
        "ru": (
            "Похоже, этого недостаточно — пожалуйста, укажите больше деталей, "
            "например город и район, минимум в двух строках:\n\n{template}"
        ),
    },
    "checkout.address_template": {
        "en": "Full name\nPhone number\nStreet address\nCity and postal code\nCountry",
        "ru": "Полное имя\nНомер телефона\nАдрес\nГород и почтовый индекс\nСтрана",
    },
    "checkout.active_order_rejected": {
        "en": "Order #{id} was rejected.",
        "ru": "Заказ №{id} был отклонён.",
    },
    "checkout.reject_reason_line": {"en": "Reason: {reason}", "ru": "Причина: {reason}"},
    "checkout.resubmit_hash_button": {
        "en": "Resubmit transaction hash",
        "ru": "Отправить хэш транзакции повторно",
    },
    "checkout.cancel_order_id_button": {"en": "Cancel order #{id}", "ru": "Отменить заказ №{id}"},
    "checkout.cancel_order_button": {"en": "Cancel order", "ru": "Отменить заказ"},
    "checkout.already_active_order": {
        "en": "You already have an active order.\n\n{order_heading} — {status}",
        "ru": "У вас уже есть активный заказ.\n\n{order_heading} — {status}",
    },
    "checkout.view_order_button": {"en": "View order", "ru": "Посмотреть заказ"},
    "checkout.payment_method_unavailable": {
        "en": "That payment method is no longer available — please choose another.",
        "ru": "Этот способ оплаты больше недоступен — пожалуйста, выберите другой.",
    },
    "checkout.choose_payment_method_button": {
        "en": "Choose payment method",
        "ru": "Выбрать способ оплаты",
    },
    "checkout.items_removed": {
        "en": "{count} item(s) removed from this order — no longer available.",
        "ru": "Из заказа удалено позиций: {count} (больше не в наличии).",
    },
    "checkout.order_heading": {"en": "Order #{id}", "ru": "Заказ №{id}"},
    "checkout.choose_asset": {
        "en": "Choose the crypto asset you'd like to pay with:",
        "ru": "Выберите криптовалюту для оплаты:",
    },
    "checkout.choose_network": {
        "en": "Choose the network for {asset}:",
        "ru": "Выберите сеть для {asset}:",
    },
    "checkout.back_button": {"en": "Back", "ru": "Назад"},
    "checkout.pay_amount": {
        "en": "Send the equivalent of this amount in {asset} at the current market rate.",
        "ru": "Отправьте эквивалент этой суммы в {asset} по текущему рыночному курсу.",
    },
    "checkout.network_label": {"en": "Network: {network}", "ru": "Сеть: {network}"},
    "checkout.memo_warning": {
        "en": "⚠️ This payment requires the memo/tag above — sending without it may result in lost funds.",
        "ru": "⚠️ Для этого платежа обязателен указанный выше memo/tag — без него средства могут быть утеряны.",
    },
    "checkout.pay_by": {
        "en": "Please send payment by {expires} UTC.",
        "ru": "Пожалуйста, отправьте оплату до {expires} UTC.",
    },
    "checkout.proof_note": {
        "en": "Once paid, send the transaction hash below (preferred), or a payment screenshot if that's easier.",
        "ru": "После оплаты отправьте хэш транзакции ниже (предпочтительно) или скриншот платежа, если так удобнее.",
    },
    "checkout.hash_button": {
        "en": "I've paid — send transaction hash",
        "ru": "Я оплатил — отправить хэш транзакции",
    },
    "checkout.hash_prompt": {
        "en": (
            "Send the transaction hash (TXID) for your payment, or a screenshot if "
            "that's easier.\n\nYou'll usually find the hash in your wallet's "
            "transaction history or activity log right after sending — it's a long "
            "string of letters and numbers."
        ),
        "ru": (
            "Отправьте хэш транзакции (TXID) для вашего платежа или скриншот, если "
            "так удобнее.\n\nОбычно хэш можно найти в истории транзакций вашего "
            "кошелька сразу после отправки — это длинная строка из букв и цифр."
        ),
    },
    "checkout.back_to_payment_details_button": {
        "en": "Back to payment details",
        "ru": "Назад к деталям оплаты",
    },
    "checkout.back_to_order_details_button": {
        "en": "Back to order details",
        "ru": "Назад к деталям заказа",
    },
    "checkout.request_expired": {
        "en": "This request has expired — please try again.",
        "ru": "Срок действия запроса истёк — попробуйте ещё раз.",
    },
    "checkout.duplicate_hash": {
        "en": (
            "That transaction hash is already attached to another order. Please "
            "double-check it and send the correct one, or contact the shop owner."
        ),
        "ru": (
            "Этот хэш транзакции уже привязан к другому заказу. Пожалуйста, "
            "проверьте его и отправьте правильный, либо свяжитесь с владельцем магазина."
        ),
    },
    "checkout.hash_too_long": {
        "en": "That's too long to be a transaction hash (max {max} characters). Please double-check it and send it again.",
        "ru": "Это слишком длинно для хэша транзакции (максимум {max} символов). Проверьте и отправьте снова.",
    },
    "checkout.hash_mismatch": {
        "en": "That doesn't look like a {network} transaction hash:\n\n{hash}\n\nYou can send it anyway if you're sure it's correct.",
        "ru": "Это не похоже на хэш транзакции {network}:\n\n{hash}\n\nВы можете отправить его всё равно, если уверены, что он верный.",
    },
    "checkout.send_anyway_button": {"en": "Send it anyway", "ru": "Всё равно отправить"},
    "checkout.try_again_button": {"en": "Try again", "ru": "Попробовать снова"},
    "checkout.hash_submitted": {
        "en": "Got it! {order_heading} is now awaiting verification.\n\nWe'll confirm within a few hours.",
        "ru": "Принято! {order_heading} теперь ожидает проверки.\n\nМы подтвердим в течение нескольких часов.",
    },
    "checkout.hash_no_longer_accept": {
        "en": "This order can no longer accept a hash.",
        "ru": "Этот заказ больше не может принять хэш.",
    },
    "checkout.cancel_confirm_ask": {
        "en": "Cancel order #{id}? This cannot be undone.",
        "ru": "Отменить заказ №{id}? Это действие нельзя отменить.",
    },
    "checkout.yes_cancel_button": {"en": "Yes, cancel", "ru": "Да, отменить"},
    "checkout.keep_it_button": {"en": "No, keep it", "ru": "Нет, оставить"},
    "checkout.order_cancelled": {
        "en": "Order #{id} has been cancelled. Your cart items have been restored.",
        "ru": "Заказ №{id} отменён. Товары возвращены в вашу корзину.",
    },
    "checkout.cancel_refused": {
        "en": "This order can no longer be cancelled here — please contact the shop owner if you need help.",
        "ru": "Этот заказ больше нельзя отменить здесь — пожалуйста, свяжитесь с владельцем магазина.",
    },
    "checkout.no_orders_yet": {
        "en": "You haven't placed any orders yet.",
        "ru": "У вас пока нет заказов.",
    },
    "checkout.your_orders_heading": {"en": "Your orders", "ru": "Ваши заказы"},
    "checkout.order_list_label": {
        "en": "#{id} — {date} — {total} — {status}",
        "ru": "№{id} — {date} — {total} — {status}",
    },
    "checkout.page_label": {"en": "Page {page}/{total}", "ru": "Стр. {page}/{total}"},
    "checkout.view_payment_details_button": {
        "en": "View payment details",
        "ru": "Посмотреть детали оплаты",
    },
    "checkout.submitted_hash": {"en": "Submitted hash: {hash}", "ru": "Отправленный хэш: {hash}"},
    "checkout.screenshot_submitted": {
        "en": "Payment screenshot submitted.",
        "ru": "Скриншот платежа отправлен.",
    },
    "checkout.verification_pending": {
        "en": "Verification is pending — we'll confirm within a few hours.",
        "ru": "Проверка выполняется — мы подтвердим в течение нескольких часов.",
    },
    "checkout.payment_confirmed_need_address": {
        "en": "Payment confirmed! Please provide your shipping address.",
        "ru": "Оплата подтверждена! Пожалуйста, укажите адрес доставки.",
    },
    "checkout.enter_shipping_address_button": {
        "en": "Enter shipping address",
        "ru": "Указать адрес доставки",
    },
    "checkout.tracking_note_line": {
        "en": "Tracking note: {note}",
        "ru": "Информация о доставке: {note}",
    },
    "checkout.verified_confirmed": {
        "en": "Great news! Payment for {order_heading} has been confirmed.\n\nYour order is confirmed and will be dispatched soon.",
        "ru": "Отличная новость! Оплата заказа {order_heading} подтверждена.\n\nВаш заказ подтверждён и скоро будет отправлен.",
    },
    "checkout.rejected": {
        "en": "Unfortunately {order_heading} could not be verified.\n\nReason: {reason}\n\nYou're welcome to resubmit your transaction hash if you believe this is a mistake.",
        "ru": "К сожалению, оплата {order_heading} не подтверждена.\n\nПричина: {reason}\n\nВы можете отправить хэш транзакции ещё раз, если считаете, что это ошибка.",
    },
    "checkout.send_another_hash_button": {
        "en": "Send another transaction hash",
        "ru": "Отправить другой хэш транзакции",
    },
    "checkout.admin_cancelled": {
        "en": "{order_heading} has been cancelled. If you have questions, please contact us.",
        "ru": "{order_heading} отменён. Если у вас есть вопросы, пожалуйста, свяжитесь с нами.",
    },
    "checkout.shipped": {
        "en": "{order_heading} is on its way!",
        "ru": "{order_heading} уже в пути!",
    },
    "checkout.delivered": {
        "en": "Your {order_heading} has been delivered. Thank you for shopping with us!",
        "ru": "Ваш {order_heading} доставлен. Спасибо за покупку!",
    },
    # -- order status labels (customer-facing) -------------------------------
    "status.awaiting_payment": {"en": "Awaiting payment", "ru": "Ожидает оплаты"},
    "status.awaiting_verification": {"en": "Verification pending", "ru": "Ожидает проверки"},
    "status.awaiting_shipping": {
        "en": "Payment confirmed — preparing to ship",
        "ru": "Оплата подтверждена — готовится к отправке",
    },
    "status.confirmed": {"en": "Confirmed", "ru": "Подтверждён"},
    "status.shipped": {"en": "Shipped", "ru": "Отправлен"},
    "status.delivered": {"en": "Delivered", "ru": "Доставлен"},
    "status.rejected": {"en": "Payment rejected", "ru": "Оплата отклонена"},
    "status.cancelled": {"en": "Cancelled", "ru": "Отменён"},
    "status.expired": {"en": "Expired", "ru": "Истёк срок"},
    # -- shipping.py: legacy post-payment address flow (compat only) --------
    "shipping.prompt": {
        "en": "Please send your shipping address as a single message, with each detail on its own line:\n\n{template}",
        "ru": "Пожалуйста, отправьте адрес доставки одним сообщением, каждую деталь на отдельной строке:\n\n{template}",
    },
    "shipping.full_template": {
        "en": "Full name\nPhone number\nStreet address\nCity and postal code\nCountry",
        "ru": "Полное имя\nНомер телефона\nАдрес\nГород и почтовый индекс\nСтрана",
    },
    "shipping.use_previous_button": {
        "en": "Use my previous address",
        "ru": "Использовать предыдущий адрес",
    },
    "shipping.my_orders_button": {"en": "My orders", "ru": "Мои заказы"},
    "shipping.cannot_accept_address": {
        "en": "This order can no longer accept an address.",
        "ru": "Этот заказ больше не может принять адрес.",
    },
    "shipping.confirm_heading": {
        "en": "Please confirm your shipping address:",
        "ru": "Пожалуйста, подтвердите адрес доставки:",
    },
    "shipping.confirm_button": {"en": "Confirm", "ru": "Подтвердить"},
    "shipping.reenter_button": {"en": "Re-enter", "ru": "Ввести заново"},
    "shipping.too_short": {
        "en": "That looks a bit short — please include name, phone, full address, city and country, for example:\n\n{template}",
        "ru": "Похоже, этого недостаточно — укажите имя, телефон, полный адрес, город и страну, например:\n\n{template}",
    },
    "shipping.too_long": {
        "en": "That's too long for a shipping address (max {max} characters). Please shorten it and send it again.",
        "ru": "Слишком длинный адрес доставки (максимум {max} символов). Сократите и отправьте снова.",
    },
    "shipping.no_previous_alert": {
        "en": "No previous address on file.",
        "ru": "Предыдущий адрес не найден.",
    },
    "shipping.request_expired": {
        "en": "This request has expired — please send your address again.",
        "ru": "Срок действия запроса истёк — отправьте адрес ещё раз.",
    },
    "shipping.enter_address_button": {"en": "Enter address", "ru": "Указать адрес"},
    "shipping.confirmed": {
        "en": "Thank you! {order_heading} is confirmed and will be shipped soon.",
        "ru": "Спасибо! {order_heading} подтверждён и скоро будет отправлен.",
    },
    # -- jobs.py: reminders and expiry ---------------------------------------
    "jobs.reminder_final": {
        "en": "This is your last reminder before this order is automatically cancelled — you'll need to start a new order if it isn't paid soon.",
        "ru": "Это последнее напоминание перед автоматической отменой заказа — если оплата не поступит в ближайшее время, потребуется оформить новый заказ.",
    },
    "jobs.reminder_first": {
        "en": "Just a friendly reminder — this order is still awaiting payment.",
        "ru": "Небольшое напоминание — этот заказ всё ещё ожидает оплаты.",
    },
    "jobs.reminder_again": {
        "en": "Still waiting on payment for this order.",
        "ru": "Всё ещё ожидаем оплату по этому заказу.",
    },
    "jobs.time_left_less_than_hour": {
        "en": "less than an hour left before this order expires",
        "ru": "менее часа до истечения срока заказа",
    },
    "jobs.time_left_hours": {
        "en": "about {hours} {hour_word} left before this order expires",
        "ru": "около {hours} ч. до истечения срока заказа",
    },
    "jobs.hour_singular": {"en": "hour", "ru": "час"},
    "jobs.hour_plural": {"en": "hours", "ru": "часов"},
    "jobs.expired": {
        "en": "{order_heading} has expired and been cancelled. Feel free to start a new order anytime.",
        "ru": "Срок заказа {order_heading} истёк, и он был отменён. Вы можете оформить новый заказ в любое время.",
    },
}


def t(key: str, lang: str, **kwargs: object) -> str:
    entry = STRINGS.get(key)
    if entry is None:
        logger.warning("i18n: missing key %r", key)
        return key

    template = entry.get(lang)
    if template is None:
        logger.warning(
            "i18n: missing language %r for key %r, falling back to %r", lang, key, DEFAULT_LANGUAGE
        )
        template = entry.get(DEFAULT_LANGUAGE)
        if template is None:
            logger.warning("i18n: key %r has no %r fallback either", key, DEFAULT_LANGUAGE)
            return key

    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        logger.warning("i18n: format error for key %r lang %r kwargs %r", key, lang, kwargs)
        return template


def status_label(status: str, lang: str) -> str:
    return t(f"status.{status}", lang)
