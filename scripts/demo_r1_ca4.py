from datetime import datetime
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123456:offline-demo')

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.database.models import SAUser, PinHistory, SalePhotoReport
from app.services.user_service import (
    sync_group_members_from_list,
    register_or_update_user,
    get_missing_sale_photo_users_for_interval,
    count_submitted_users_for_interval,
    create_maps_link,
)
from app.services.formatter import sale_photo_missing_alert_message
from app.services.shift_config import REGULAR_TITLE

GROUP_ID = -1001234567890
GROUP_NAME = 'R1-SA-CA4'
START = datetime(2026, 7, 15, 13, 0)
END = datetime(2026, 7, 15, 13, 20)

engine = create_engine('sqlite+pysqlite:///:memory:')
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
db = Session()

sync_group_members_from_list(db, GROUP_ID, GROUP_NAME)
# Bind the real no-username user from the screenshot to his actual Telegram ID.
register_or_update_user(db, 90019581, 'SEANGSALIK Specialist CA4', None, GROUP_ID, GROUP_NAME)
users = {u.name: u for u in db.query(SAUser).filter(SAUser.group_id == GROUP_ID).all()}

seang = users['SEANGSALIK Specialist CA4']
leader = users['Boeun Phally ( SA Leader)CA4']
sey = users['Sey Sopheak SA-CA4']
water = users['KOSAL PANHA ( SA Water-CA4 )']


def add_pin(user, at):
    db.add(PinHistory(
        telegram_user_id=user.telegram_user_id,
        group_id=GROUP_ID,
        group_name=GROUP_NAME,
        name=user.name,
        username=user.username,
        lat=11.5564,
        lon=104.9282,
        map_link=create_maps_link(11.5564, 104.9282),
        pinned_at=at,
        window_start=START,
        window_end=END,
    ))


def add_photo_or_form(user, at, caption='Photo'):
    db.add(SalePhotoReport(
        telegram_user_id=user.telegram_user_id,
        group_id=GROUP_ID,
        group_name=GROUP_NAME,
        name=user.name,
        username=user.username,
        caption=caption,
        reply_lat=None,
        reply_lon=None,
        reply_map_link=None,
        telegram_message_id=100,
        replied_message_id=None,
        submitted_at=at,
        window_start=START,
        window_end=END,
    ))

# 1) SEANGSALIK sends Location and Photo separately: VALID, must NOT be alerted.
add_pin(seang, datetime(2026, 7, 15, 13, 12, 0))
add_photo_or_form(seang, datetime(2026, 7, 15, 13, 12, 5), 'Outlet photo')

# 2) Team Leader sends a text-form and GPS: VALID, must NOT be alerted.
add_photo_or_form(leader, datetime(2026, 7, 15, 13, 10, 0), 'ID:9001 Dealer:CA4 Outlet Name:Coffee Shop')
add_pin(leader, datetime(2026, 7, 15, 13, 11, 0))

# 3) Sey sends GPS only: INVALID, must be alerted.
add_pin(sey, datetime(2026, 7, 15, 13, 12, 0))

# 4) SA Water sends Photo only: INVALID, must be alerted.
add_photo_or_form(water, datetime(2026, 7, 15, 13, 12, 0), 'Outlet photo')

db.commit()

print('=== DRY-RUN INPUT ===')
print('SEANGSALIK: GPS + separate Photo -> expected SUBMITTED')
print('Boeun Phally: Text form + GPS -> expected SUBMITTED')
print('Sey Sopheak: GPS only -> expected MISSING')
print('KOSAL PANHA: Photo only -> expected MISSING')
print()

for flow_key, label in [('sa', '07:30 FLOW'), ('sa_630', '06:30 FLOW')]:
    missing = get_missing_sale_photo_users_for_interval(db, GROUP_ID, START, END, flow_key=flow_key)
    submitted = count_submitted_users_for_interval(db, GROUP_ID, START, END, flow_key=flow_key)
    for u in missing:
        u.miss_count_today = 1
    print(f'=== {label} CHECK: 01:00 PM - 01:20 PM ===')
    print(f'Submitted: {submitted}')
    print(f'Missing: {len(missing)}')
    print('Missing names:', ', '.join(u.name for u in missing) or 'None')
    print()
    if missing:
        print(sale_photo_missing_alert_message(
            missing,
            'Asia/Phnom_Penh',
            START,
            END,
            GROUP_NAME,
            title=REGULAR_TITLE,
            submitted_count=submitted,
            total_users=submitted + len(missing),
        ))
    else:
        print('SILENT: all users submitted')
    print('\n' + '-' * 72 + '\n')

# Strong assertions: the wrong-alert case must be fixed.
sa_missing_names = {u.name for u in get_missing_sale_photo_users_for_interval(db, GROUP_ID, START, END, flow_key='sa')}
water_missing_names = {u.name for u in get_missing_sale_photo_users_for_interval(db, GROUP_ID, START, END, flow_key='sa_630')}
assert 'SEANGSALIK Specialist CA4' not in sa_missing_names
assert 'Sey Sopheak SA-CA4' in sa_missing_names
assert 'Boeun Phally ( SA Leader)CA4' not in water_missing_names
assert 'KOSAL PANHA ( SA Water-CA4 )' in water_missing_names
print('DEMO TEST PASSED: correct users alerted; SEANGSALIK was not falsely alerted.')
