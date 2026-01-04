import asyncio
import json
import os
import sys
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Добавляем корень проекта в путь поиска
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import get_db_session
from app.db.models import Equivalent, Participant, TrustLine

async def seed_data():
    """
    Заполняет базу данных начальными данными из JSON файлов в папке seeds/.
    """
    seeds_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'seeds')
    
    # 1. Загрузка эквивалентов
    equivalents_path = os.path.join(seeds_dir, 'equivalents.json')
    participants_path = os.path.join(seeds_dir, 'participants.json')
    trustlines_path = os.path.join(seeds_dir, 'trustlines.json')

    async for session in get_db_session():
        print("Starting seeding process...")
        try:
            # === Equivalents ===
            if os.path.exists(equivalents_path):
                with open(equivalents_path, 'r', encoding='utf-8') as f:
                    equivalents_data = json.load(f)
                    
                for eq_data in equivalents_data:
                    # Проверяем существование
                    stmt = select(Equivalent).where(Equivalent.code == eq_data['code'])
                    result = await session.execute(stmt)
                    existing = result.scalar_one_or_none()
                    
                    if not existing:
                        eq = Equivalent(**eq_data)
                        session.add(eq)
                        print(f"Adding Equivalent: {eq_data['code']}")
                    else:
                        print(f"Equivalent {eq_data['code']} already exists.")
                await session.flush() # Чтобы получить ID для следующих шагов

            # === Participants ===
            if os.path.exists(participants_path):
                with open(participants_path, 'r', encoding='utf-8') as f:
                    participants_data = json.load(f)

                for p_data in participants_data:
                    stmt = select(Participant).where(Participant.pid == p_data['pid'])
                    result = await session.execute(stmt)
                    existing = result.scalar_one_or_none()
                    
                    if not existing:
                        p = Participant(**p_data)
                        session.add(p)
                        print(f"Adding Participant: {p_data['display_name']}")
                    else:
                        print(f"Participant {p_data['pid']} already exists.")
                await session.flush()

            # === Trust Lines ===
            # TrustLines требуют ID участников и эквивалентов. 
            # В seeds/trustlines.json предполагается использование pid и equivalent_code для связи.
            if os.path.exists(trustlines_path):
                with open(trustlines_path, 'r', encoding='utf-8') as f:
                    trustlines_data = json.load(f)

                for tl_data in trustlines_data:
                    # Находим ID по уникальным полям
                    from_pid = tl_data.pop('from_pid')
                    to_pid = tl_data.pop('to_pid')
                    eq_code = tl_data.pop('equivalent_code')

                    stmt_from = select(Participant).where(Participant.pid == from_pid)
                    res_from = await session.execute(stmt_from)
                    p_from = res_from.scalar_one_or_none()

                    stmt_to = select(Participant).where(Participant.pid == to_pid)
                    res_to = await session.execute(stmt_to)
                    p_to = res_to.scalar_one_or_none()

                    stmt_eq = select(Equivalent).where(Equivalent.code == eq_code)
                    res_eq = await session.execute(stmt_eq)
                    eq = res_eq.scalar_one_or_none()

                    if p_from and p_to and eq:
                         # Проверяем существование линии
                        stmt_tl = select(TrustLine).where(
                            TrustLine.from_participant_id == p_from.id,
                            TrustLine.to_participant_id == p_to.id,
                            TrustLine.equivalent_id == eq.id
                        )
                        res_tl = await session.execute(stmt_tl)
                        existing_tl = res_tl.scalar_one_or_none()

                        if not existing_tl:
                            tl = TrustLine(
                                from_participant_id=p_from.id,
                                to_participant_id=p_to.id,
                                equivalent_id=eq.id,
                                **tl_data
                            )
                            session.add(tl)
                            print(f"Adding TrustLine: {from_pid} -> {to_pid} ({eq_code})")
                        else:
                            print(f"TrustLine {from_pid} -> {to_pid} ({eq_code}) already exists.")
                    else:
                        print(f"Skipping TrustLine {from_pid} -> {to_pid} ({eq_code}): Missing dependencies.")

            await session.commit()
            print("Seeding completed successfully.")
            
        except Exception as e:
            await session.rollback()
            print(f"Seeding failed: {e}")
            raise
        break # Exit loop after one session usage

if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(seed_data())