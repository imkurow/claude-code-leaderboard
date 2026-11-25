"""Achievements: badges unlocked from the usage the leaderboard already has.

Everything here is a pure function of aggregated numbers (hourly output, totals, models), so it
works the same for Claude Code telemetry, imported history and 9router. server.py feeds it once a
minute and stores each first unlock, so a badge stays earned.

Tiers: bronze, silver, gold, legend. A secret badge shows as "???" until someone unlocks it.
"""
from collections import defaultdict
from datetime import datetime, timedelta

M = 1_000_000

# id, tier, "glyph|ribbon label", secret, English name, Indonesian name, English description, Indonesian description
# (the glyph is drawn by the page; the label, if any, goes on the badge's ribbon)
CATALOG = [
    # ---- milestones: total output tokens
    ("first_words", "bronze", "sprout|", False, "First Words", "Langkah Pertama",
     "Get your first output token on the board.", "Dapat output token pertama di leaderboard."),
    ("hundred_k", "bronze", "quill|100K", False, "Hundred Thousand", "Seratus Ribu Kata",
     "Reach 100K output tokens in total.", "Tembus 100 ribu output token."),
    ("millionaire", "bronze", "diamond|1M", False, "Token Millionaire", "Jutawan Token",
     "Reach 1M output tokens in total.", "Tembus 1 juta output token."),
    ("ten_million", "silver", "gear|10M", False, "Word Factory", "Pabrik Kata",
     "Reach 10M output tokens in total.", "Tembus 10 juta output token."),
    ("fifty_million", "gold", "rocket|50M", False, "Escape Velocity", "Kecepatan Lepas",
     "Reach 50M output tokens in total.", "Tembus 50 juta output token."),
    ("hundred_million", "gold", "galaxy|100M", False, "Hundred Million", "Seratus Juta",
     "Reach 100M output tokens in total.", "Tembus 100 juta output token."),
    ("half_billion", "legend", "columns|500M", False, "Living Legend", "Legenda Hidup",
     "Reach 500M output tokens in total.", "Tembus 500 juta output token."),
    ("billion", "legend", "wings|1B", False, "Myth", "Mitos",
     "Reach 1 billion output tokens in total.", "Tembus 1 miliar output token."),
    # ---- big days and hours
    ("lightning_day", "bronze", "bolt|1M", False, "Lightning Day", "Hari Kilat",
     "Write 1M output tokens in a single day.", "Hasilkan 1 juta output token dalam sehari."),
    ("token_storm", "silver", "tornado|5M", False, "Token Storm", "Badai Token",
     "Write 5M output tokens in a single day.", "Hasilkan 5 juta output token dalam sehari."),
    ("hurricane", "gold", "spiral|10M", False, "Hurricane", "Topan",
     "Write 10M output tokens in a single day.", "Hasilkan 10 juta output token dalam sehari."),
    ("rush_hour", "silver", "burst|500K", False, "Rush Hour", "Satu Jam Gila",
     "Write 500K output tokens within one hour.", "Hasilkan 500 ribu output token dalam satu jam."),
    ("million_hour", "gold", "atom|1M", False, "Meltdown", "Reaktor Meleleh",
     "Write 1M output tokens within one hour.", "Hasilkan 1 juta output token dalam satu jam."),
    ("marathon", "silver", "hourglass|18", False, "Marathon", "Maraton",
     "Be active in 18 different hours of the same day.", "Aktif di 18 jam berbeda dalam satu hari."),
    ("insomnia", "gold", "eye|22", False, "Insomnia", "Insomnia",
     "Be active in 22 different hours of the same day.", "Aktif di 22 jam berbeda dalam satu hari."),
    # ---- streaks
    ("streak_3", "bronze", "flame|3", False, "Warming Up", "Pemanasan",
     "Use Claude 3 days in a row.", "Pakai Claude 3 hari berturut-turut."),
    ("streak_7", "silver", "flame|7", False, "Full Week", "Seminggu Penuh",
     "Use Claude 7 days in a row.", "Pakai Claude 7 hari berturut-turut."),
    ("streak_14", "silver", "flame|14", False, "Fortnight", "Dua Pekan",
     "Use Claude 14 days in a row.", "Pakai Claude 14 hari berturut-turut."),
    ("streak_30", "gold", "flame|30", False, "No Days Off", "Sebulan Tanpa Libur",
     "Use Claude 30 days in a row.", "Pakai Claude 30 hari berturut-turut."),
    ("streak_100", "legend", "flame|100", False, "Hundred Days", "Seratus Hari",
     "Use Claude 100 days in a row.", "Pakai Claude 100 hari berturut-turut."),
    ("comeback", "bronze", "mountain|", False, "Back From the Mountain", "Pulang Bertapa",
     "Come back after 14 days or more without usage.", "Kembali setelah 14 hari atau lebih tanpa pemakaian."),
    # ---- when you work
    ("night_owl", "bronze", "moon|", False, "Night Owl", "Burung Hantu",
     "Use Claude between midnight and 4 AM.", "Pakai Claude antara jam 00.00 dan 04.00."),
    ("early_bird", "bronze", "sunrise|", False, "Early Bird", "Ayam Jago",
     "Use Claude between 4 and 6 AM.", "Pakai Claude antara jam 04.00 dan 06.00."),
    ("overtime", "silver", "clock|25", False, "Overtime", "Lembur Terus",
     "Use Claude after 9 PM on 25 different days.", "Pakai Claude lewat jam 21.00 di 25 hari berbeda."),
    ("lunch_desk", "bronze", "fork|20", False, "Lunch at the Desk", "Makan Siang di Depan Laptop",
     "Use Claude between 12 and 1 PM on 20 different days.", "Pakai Claude jam 12.00-13.00 di 20 hari berbeda."),
    ("monday_spirit", "bronze", "sun|", False, "Monday Spirit", "Senin Semangat",
     "Use Claude before 8 AM on 4 different Mondays.", "Pakai Claude sebelum jam 08.00 di 4 hari Senin berbeda."),
    ("weekend_warrior", "silver", "gamepad|", False, "Weekend Warrior", "Pejuang Akhir Pekan",
     "Use Claude on both Saturday and Sunday of 4 different weekends.", "Pakai Claude di hari Sabtu dan Minggu pada 4 akhir pekan berbeda."),
    ("seven_days", "bronze", "calendar|", False, "Seven Days a Week", "Tujuh Hari Seminggu",
     "Use Claude every day from Monday to Sunday of one week.", "Pakai Claude setiap hari dari Senin sampai Minggu dalam satu minggu."),
    ("around_the_clock", "legend", "clock24|24", False, "Around the Clock", "Sepanjang Waktu",
     "Be active in every one of the 24 hours of the day within a single week.", "Aktif di tiap jam dari 24 jam dalam satu minggu yang sama."),
    ("month_end_sprint", "silver", "flag|", False, "Month-End Sprint", "Kebut Akhir Bulan",
     "Make the last day of a month your best day of that month (active on 10+ days that month).", "Jadikan hari terakhir bulan itu hari terbaikmu di bulan itu (aktif minimal 10 hari)."),
    ("friday_deploy", "silver", "anchor|", True, "Friday Night Deploy", "Deploy Jumat Malam",
     "Use Claude after 10 PM on 3 different Fridays. Brave.", "Pakai Claude lewat jam 22.00 di 3 hari Jumat berbeda. Berani."),
    ("twin_date", "bronze", "twin|", True, "Twin Date", "Tanggal Kembar",
     "Use Claude on a twin date like 11/11 or 12/12.", "Pakai Claude di tanggal kembar seperti 11/11 atau 12/12."),
    ("anniversary", "gold", "gift|", True, "Anniversary", "Setahun Bersama",
     "Use Claude on the day you have been on the board for a year.", "Pakai Claude tepat setahun sejak ikut leaderboard."),
    ("new_year", "gold", "fireworks|", True, "New Year's Coder", "Ngoding Pas Tahun Baru",
     "Use Claude between 11 PM on 31 December and 1 AM on 1 January.", "Pakai Claude antara jam 23.00 malam tahun baru dan jam 01.00 tanggal 1 Januari."),
    ("same_day", "bronze", "target|", False, "Straight Out of the Gate", "Langsung Gas",
     "Have output on the day you joined.", "Ada output di hari pertama ikut."),
    # ---- models
    ("haiku_poet", "silver", "leaf|1M", False, "Haiku Poet", "Penyair Haiku",
     "Write 1M output tokens with Haiku models.", "Hasilkan 1 juta output token dengan model Haiku."),
    ("sonnet_master", "gold", "note|25M", False, "Sonnet Master", "Maestro Soneta",
     "Write 25M output tokens with Sonnet models.", "Hasilkan 25 juta output token dengan model Sonnet."),
    ("magnum_opus", "gold", "crown|25M", False, "Magnum Opus", "Magnum Opus",
     "Write 25M output tokens with Opus models.", "Hasilkan 25 juta output token dengan model Opus."),
    ("trilogy", "bronze", "trinity|", False, "The Trilogy", "Trilogi",
     "Use Haiku, Sonnet and Opus.", "Pakai Haiku, Sonnet, dan Opus."),
    ("explorer", "silver", "compass|", False, "Model Explorer", "Penjelajah Model",
     "Use 5 different models.", "Pakai 5 model yang berbeda."),
    ("loyal", "silver", "ring|", False, "One True Model", "Setia Satu Model",
     "Write 1M output tokens with 95% or more on one model.", "Hasilkan 1 juta output token dengan 95% atau lebih dari satu model."),
    ("free_rider", "silver", "tag|1M", False, "Free Rider", "Gratisan Sejati",
     "Write 1M output tokens with free models.", "Hasilkan 1 juta output token dengan model gratis."),
    # ---- context and cache
    ("cache_king", "bronze", "database|1B", False, "Cache King", "Raja Cache",
     "Read 1 billion tokens from the cache.", "Baca 1 miliar token dari cache."),
    ("elephant", "gold", "chip|10B", False, "An Elephant Never Forgets", "Gajah Tak Pernah Lupa",
     "Read 10 billion tokens from the cache.", "Baca 10 miliar token dari cache."),
    ("alexandria", "legend", "books|25B", False, "Library of Alexandria", "Perpustakaan Alexandria",
     "Read 25 billion tokens from the cache.", "Baca 25 miliar token dari cache."),
    ("context_architect", "silver", "setsquare|100M", False, "Context Architect", "Arsitek Konteks",
     "Write 100M tokens to the cache.", "Tulis 100 juta token ke cache."),
    ("bookworm", "silver", "openbook|100M", False, "Bookworm", "Kutu Buku",
     "Send 100M fresh input tokens.", "Kirim 100 juta input token baru."),
    # ---- cost, sessions, time
    ("pricey_coffee", "bronze", "cup|$100", False, "Pricey Coffee", "Kopi Mahal",
     "Reach $100 in estimated cost.", "Tembus estimasi biaya $100."),
    ("sultan", "gold", "coins|$1K", False, "Sultan", "Sultan",
     "Reach $1,000 in estimated cost.", "Tembus estimasi biaya $1.000."),
    ("whale", "legend", "tail|$10K", False, "Whale", "Paus",
     "Reach $10,000 in estimated cost.", "Tembus estimasi biaya $10.000."),
    ("hundred_sessions", "bronze", "play|100", False, "Hundred Sessions", "Seratus Sesi",
     "Start 100 Claude Code sessions.", "Buka 100 sesi Claude Code."),
    ("thousand_sessions", "gold", "film|1K", False, "Thousand Sessions", "Seribu Sesi",
     "Start 1,000 Claude Code sessions.", "Buka 1.000 sesi Claude Code."),
    ("elite", "gold", "terminal|1337", True, "1337", "1337",
     "Start 1,337 sessions. Elite.", "Buka 1.337 sesi. Elite."),
    ("first_flight", "bronze", "plane|10", False, "First Flight", "Penerbangan Perdana",
     "Record 10 hours of active time.", "Catat 10 jam waktu aktif."),
    ("flight_hours", "silver", "stopwatch|100", False, "Flight Hours", "Jam Terbang",
     "Record 100 hours of active time.", "Catat 100 jam waktu aktif."),
    # ---- setup
    ("photogenic", "bronze", "camera|", False, "Photogenic", "Fotogenik",
     "Upload a profile photo.", "Pasang foto profil."),
    ("two_machines", "bronze", "monitors|", False, "Two Machines", "Dua Laptop",
     "Send history from two different computers.", "Kirim riwayat dari dua komputer berbeda."),
    ("back_roads", "bronze", "road|", False, "Back Roads", "Jalan Tikus",
     "Join through another harness: 9router, JRouter, Hermes or OpenRouter.", "Ikut lewat harness lain: 9router, JRouter, Hermes, atau OpenRouter."),
    ("router_rider", "silver", "antenna|1K", False, "Router Rider", "Penunggang Router",
     "Send 1,000 requests through 9router, JRouter, Hermes or OpenRouter.", "Kirim 1.000 request lewat 9router, JRouter, Hermes, atau OpenRouter."),
    # ---- standing against the others (completed days, weeks and months only)
    ("daily_champion", "silver", "medal1|", False, "Champion of the Day", "Juara Harian",
     "Finish a day in first place.", "Selesai di peringkat 1 pada suatu hari."),
    ("weekly_podium", "bronze", "podium|", False, "Weekly Podium", "Podium Mingguan",
     "Finish a week (Monday to Sunday) in the top 3.", "Selesai di 3 besar dalam satu minggu (Senin-Minggu)."),
    ("weekly_king", "gold", "trophy|", False, "King of the Week", "Raja Pekan",
     "Finish a week in first place.", "Selesai di peringkat 1 dalam satu minggu."),
    ("monthly_champion", "legend", "star|", False, "Champion of the Month", "Juara Bulanan",
     "Finish a calendar month in first place.", "Selesai di peringkat 1 dalam satu bulan kalender."),
    ("unbeatable", "legend", "shield|", False, "Unbeatable", "Tak Terkalahkan",
     "Finish first 7 days in a row.", "Juara harian 7 hari berturut-turut."),
    ("dynasty", "legend", "castle|", False, "Dynasty", "Dinasti",
     "Finish first 4 weeks in a row.", "Juara mingguan 4 minggu berturut-turut."),
    ("dark_horse", "gold", "knight|", False, "Dark Horse", "Kuda Hitam",
     "Go from outside the top 3 one week to first place the next.", "Dari luar 3 besar di satu minggu langsung juara di minggu berikutnya."),
    ("first_in", "silver", "key|", False, "Opening the Day", "Pembuka Hari",
     "Be the first one active on 10 different days.", "Jadi orang pertama yang aktif di 10 hari berbeda."),
    ("last_out", "silver", "bulb|", False, "Lights Out", "Pemadam Lampu",
     "Be the last one active on 10 different days.", "Jadi orang terakhir yang aktif di 10 hari berbeda."),
    ("in_sync", "silver", "rings|", False, "In Sync", "Kompak",
     "Be active in the same hour as a teammate on 10 different days.", "Aktif di jam yang sama dengan teman satu tim di 10 hari berbeda."),
    ("backbone", "gold", "spine|", False, "Backbone of the Team", "Tulang Punggung Tim",
     "Write half of your team's output in a week (team of 3 or more active).", "Hasilkan setengah output tim kamu dalam seminggu (tim minimal 3 orang aktif)."),
    ("rising_star", "gold", "shooting|", False, "Rising Star", "Bintang Pendatang Baru",
     "Finish a week in the top 3 within three weeks of joining.", "Masuk 3 besar mingguan dalam tiga minggu pertama sejak ikut."),
    ("first_wave", "silver", "wave|", False, "First Wave", "Angkatan Pertama",
     "Be one of the first three people on the leaderboard.", "Termasuk tiga orang pertama di leaderboard."),
    # ---- season 2: harder ones, counted only from the day the server first knew them (FRESH)
    ("season_opener", "bronze", "ticket|", False, "Season Opener", "Pembuka Musim",
     "Get output on the board after the new season of badges began.", "Dapat output di leaderboard setelah musim lencana baru dimulai."),
    ("clockwork", "silver", "metronome|14", False, "Clockwork", "Jam Dinding",
     "Write 100K output tokens every day for 14 days in a row.", "Hasilkan 100 ribu output token setiap hari selama 14 hari berturut-turut."),
    ("three_shifts", "silver", "sunmoon|", False, "Three Shifts", "Tiga Shift",
     "Work the morning (6-12), afternoon (12-18) and evening (18-24) of the same day, on 5 different days.", "Aktif pagi (06-12), siang (12-18), dan malam (18-24) di hari yang sama, sebanyak 5 hari."),
    ("juggler", "silver", "juggle|4", False, "Juggler", "Juggler",
     "Use 4 different models in a single day.", "Pakai 4 model berbeda dalam satu hari."),
    ("weekend_champion", "silver", "palm|", False, "Weekend Champion", "Juara Akhir Pekan",
     "Finish a Saturday or a Sunday in first place.", "Selesai di peringkat 1 pada hari Sabtu atau Minggu."),
    ("team_spirit", "silver", "team|", False, "One Team, One Soul", "Satu Tim Satu Jiwa",
     "Be active on the same day as every teammate (team of 3 or more), on 5 different days.", "Aktif di hari yang sama dengan semua teman satu tim (minimal 3 orang), sebanyak 5 hari."),
    ("diesel", "gold", "train|7", False, "Diesel Engine", "Mesin Diesel",
     "Write 500K output tokens every day for 7 days in a row.", "Hasilkan 500 ribu output token setiap hari selama 7 hari berturut-turut."),
    ("hattrick", "gold", "hattrick|", False, "Hat-trick", "Hattrick",
     "Finish first three days in a row.", "Juara harian 3 hari berturut-turut."),
    ("domination", "gold", "summit|", False, "Domination", "Dominasi",
     "Win a day with more output than the second and third place combined.", "Menang harian dengan output lebih besar dari gabungan peringkat 2 dan 3."),
    ("photo_finish", "gold", "photofinish|", False, "Photo Finish", "Photo Finish",
     "Win a day by less than 1% over the second place.", "Menang harian dengan selisih kurang dari 1% dari peringkat 2."),
    ("night_racer", "gold", "racecar|1M", False, "Night Racer", "Pembalap Malam",
     "Write 1M output tokens between midnight and 5 AM in one night.", "Hasilkan 1 juta output token antara jam 00.00 dan 05.00 dalam satu malam."),
    ("quantum_leap", "gold", "leap|5x", False, "Quantum Leap", "Lompatan Kuantum",
     "Have a day with 5 times your daily average of the 7 days before (that average at least 100K).", "Punya satu hari dengan output 5 kali rata-rata harianmu di 7 hari sebelumnya (rata-ratanya minimal 100 ribu)."),
    ("overtaker", "gold", "overtake|10", False, "Master Overtaker", "Penyalip Ulung",
     "Overtake someone on today's board 10 times.", "Menyalip orang lain di papan hari ini sebanyak 10 kali."),
    ("supernova", "legend", "nova|3M", False, "Supernova", "Supernova",
     "Write 3M output tokens within one hour.", "Hasilkan 3 juta output token dalam satu jam."),
    ("opus_marathon", "legend", "runner|5M", False, "Opus Marathon", "Maraton Opus",
     "Write 5M output tokens with Opus models in one day.", "Hasilkan 5 juta output token dengan model Opus dalam satu hari."),
    ("rock_bottom", "legend", "rise|", False, "From Rock Bottom", "Bangkit dari Dasar",
     "Be in last place at some hour of a day (4 or more people active) and finish that day first.", "Sempat di posisi terakhir pada suatu jam (minimal 4 orang aktif) lalu selesai di peringkat 1 di hari itu."),
    ("hundred_month", "legend", "chest|100M", False, "Hundred-Million Month", "Bulan Seratus Juta",
     "Write 100M output tokens within one calendar month.", "Hasilkan 100 juta output token dalam satu bulan kalender."),
    ("podium_regular", "legend", "podium|10", False, "Podium Regular", "Langganan Podium",
     "Finish in the daily top 3 for 10 days in a row.", "Masuk 3 besar harian 10 hari berturut-turut."),
    # ---- meta
    ("collector", "legend", "puzzle|40", False, "Collector", "Kolektor",
     "Unlock 40 other badges.", "Buka 40 lencana lain."),
]
BY_ID = {b[0]: b for b in CATALOG}
# Badges that only count usage from the day the server first saw them (not old history).
FRESH = ("season_opener", "clockwork", "three_shifts", "juggler", "weekend_champion", "team_spirit", "diesel", "hattrick",
         "domination", "photo_finish", "night_racer", "quantum_leap", "overtaker", "supernova", "opus_marathon", "rock_bottom",
         "hundred_month", "podium_regular")
TIERS = ("bronze", "silver", "gold", "legend")


def info(badge_id, lang):
    b = BY_ID[badge_id]
    glyph, _, label = b[2].partition("|")
    fl = FLAVOR.get(b[0], ("", ""))
    return {"id": b[0], "tier": b[1], "glyph": glyph, "label": label, "secret": b[3], "flavor": fl[1] if lang == "id" else fl[0],
            "name": b[5] if lang == "id" else b[4], "desc": b[7] if lang == "id" else b[6]}


def family(model):
    m = model.lower()
    for f in ("haiku", "sonnet", "opus"):
        if f in m:
            return f
    return None


def evaluate(emps, hourly, totals, models, machines, tz, now):
    """emps: {id: {"team", "created_at", "avatar"}}; hourly: {id: [(hour_ts, output)]};
    totals: {id: {"output","input","cache_read","cache_creation","cost","sessions","active","requests"}};
    models: {id: {model: output}}; machines: {id: {"computers": n, "router": bool}}.
    Returns {id: {badge: {"done", "at", "cur", "target"}}}; "at" is when it was reached, if known."""
    today = datetime.fromtimestamp(now, tz).date()
    this_week = today - timedelta(days=today.weekday())
    this_month = (today.year, today.month)
    out = {}

    # per day / week / month output for everyone, for the standings badges
    day_out = defaultdict(dict)
    per_emp_days = {}
    for e, rows in hourly.items():
        days = defaultdict(float)
        for h, v in rows:
            if v > 0:
                days[datetime.fromtimestamp(h, tz).date()] += v
        per_emp_days[e] = days
        for d, v in days.items():
            day_out[d][e] = v
    week_out, month_out = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(float))
    for d, m in day_out.items():
        wk = d - timedelta(days=d.weekday())
        for e, v in m.items():
            week_out[wk][e] += v
            month_out[(d.year, d.month)][e] += v

    def end_ts(d, days=1):
        return int(datetime.combine(d + timedelta(days=days), datetime.min.time(), tz).timestamp()) - 1

    def winners(m):
        best = max(m.values(), default=0)
        return {e for e, v in m.items() if v == best and v > 0}

    def top3(m):
        ranked = sorted((v for v in m.values() if v > 0), reverse=True)
        cut = ranked[min(2, len(ranked) - 1)] if ranked else None
        return {e for e, v in m.items() if v > 0 and cut is not None and v >= cut}

    first_day_win, first_week_win, first_month_win, first_podium = {}, {}, {}, {}
    unbeatable, rising, backbone = {}, {}, {}
    run = defaultdict(int)
    prev_day = None
    for d in sorted(day_out):
        if d >= today:
            continue
        ws = winners(day_out[d])
        if prev_day is not None and (d - prev_day).days > 1:
            run.clear()
        for e in list(run):
            if e not in ws:
                run[e] = 0
        for e in ws:
            first_day_win.setdefault(e, end_ts(d))
            run[e] += 1
            if run[e] >= 7:
                unbeatable.setdefault(e, end_ts(d))
        prev_day = d
    for wk in sorted(week_out):
        if wk >= this_week:
            continue
        m = week_out[wk]
        for e in winners(m):
            first_week_win.setdefault(e, end_ts(wk, 7))
        for e in top3(m):
            first_podium.setdefault(e, end_ts(wk, 7))
            joined = emps.get(e, {}).get("created_at")
            if joined:
                jd = datetime.fromtimestamp(joined, tz).date()
                if wk + timedelta(days=6) >= jd and wk <= jd + timedelta(days=21):
                    rising.setdefault(e, end_ts(wk, 7))
        teams = defaultdict(dict)
        for e, v in m.items():
            t = emps.get(e, {}).get("team")
            if t and v > 0:
                teams[t][e] = v
        for members in teams.values():
            if len(members) >= 3:
                total = sum(members.values())
                for e, v in members.items():
                    if v * 2 >= total:
                        backbone.setdefault(e, end_ts(wk, 7))
    for ym in sorted(month_out):
        if ym >= this_month:
            continue
        nxt = datetime(ym[0] + (ym[1] == 12), ym[1] % 12 + 1, 1, tzinfo=tz)
        for e in winners(month_out[ym]):
            first_month_win.setdefault(e, int(nxt.timestamp()) - 1)
    dynasty, dark_horse = {}, {}
    prev_top3, prev_wk, wrun = set(), None, defaultdict(int)
    for wk in sorted(week_out):
        if wk >= this_week:
            continue
        m = week_out[wk]
        ws, t3 = winners(m), top3(m)
        consecutive = prev_wk is not None and (wk - prev_wk).days == 7
        for e in list(wrun):
            if e not in ws or not consecutive:
                wrun[e] = 0
        for e in ws:
            wrun[e] += 1
            if wrun[e] >= 4:
                dynasty.setdefault(e, end_ts(wk, 7))
            if consecutive and e not in prev_top3:
                dark_horse.setdefault(e, end_ts(wk, 7))
        prev_top3, prev_wk = t3, wk
    first_in, last_out = defaultdict(list), defaultdict(list)
    span = defaultdict(dict)   # day -> {emp: (first hour, last hour)}
    hour_people = defaultdict(set)
    for e, rows in hourly.items():
        for h, v in rows:
            if v <= 0:
                continue
            d = datetime.fromtimestamp(h, tz).date()
            lo, hi = span[d].get(e, (h, h))
            span[d][e] = (min(lo, h), max(hi, h))
            hour_people[h].add(e)
    for d in sorted(span):
        if d >= today or len(span[d]) < 2:
            continue
        lo = min(v[0] for v in span[d].values())
        hi = max(v[1] for v in span[d].values())
        for e, (a, b) in span[d].items():
            if a == lo:
                first_in[e].append(end_ts(d))
            if b == hi:
                last_out[e].append(end_ts(d))
    in_sync = defaultdict(list)
    sync_days = defaultdict(set)
    for h in sorted(hour_people):
        people = hour_people[h]
        if len(people) < 2:
            continue
        d = datetime.fromtimestamp(h, tz).date()
        for e in people:
            t = emps.get(e, {}).get("team")
            if t and any(o != e and emps.get(o, {}).get("team") == t for o in people) and d not in sync_days[e]:
                sync_days[e].add(d)
                in_sync[e].append(h)
    first_wave = [e for e, _ in sorted(((e, v.get("created_at") or 0) for e, v in emps.items()), key=lambda x: (x[1], x[0]))
                  if totals.get(e, {}).get("output", 0) > 0][:3]

    for e, info_ in emps.items():
        r = {}
        rows = sorted(hourly.get(e, []))
        tot = totals.get(e, {})
        mods = models.get(e, {})

        def put(bid, done, at=None, cur=None, target=None):
            r[bid] = {"done": bool(done), "at": at if done else None,
                      "cur": cur, "target": target}

        # cumulative milestones, reached at the hour the running total crossed them
        crossings = {}
        acc = 0.0
        marks = [(1, "first_words"), (100_000, "hundred_k"), (M, "millionaire"), (10 * M, "ten_million"),
                 (50 * M, "fifty_million"), (100 * M, "hundred_million"), (500 * M, "half_billion"), (1000 * M, "billion")]
        for h, v in rows:
            if v <= 0:
                continue
            acc += v
            for limit, bid in marks:
                if acc >= limit and bid not in crossings:
                    crossings[bid] = h
        total_out = tot.get("output", 0)
        for limit, bid in marks:
            put(bid, total_out >= limit, crossings.get(bid), min(total_out, limit), limit)

        # big days / hours
        days = per_emp_days.get(e, {})
        best_day = max(days.values(), default=0)
        def first_day_over(limit):
            for d in sorted(days):
                if days[d] >= limit:
                    return end_ts(d)
            return None
        put("lightning_day", best_day >= M, first_day_over(M), min(best_day, M), M)
        put("token_storm", best_day >= 5 * M, first_day_over(5 * M), min(best_day, 5 * M), 5 * M)
        put("hurricane", best_day >= 10 * M, first_day_over(10 * M), min(best_day, 10 * M), 10 * M)
        best_hour = max((v for _, v in rows), default=0)
        for bid, limit in (("rush_hour", 500_000), ("million_hour", M)):
            put(bid, best_hour >= limit, next((h for h, v in rows if v >= limit), None), min(best_hour, limit), limit)

        hours_by_day = defaultdict(set)
        local = []
        for h, v in rows:
            if v <= 0:
                continue
            dt = datetime.fromtimestamp(h, tz)
            hours_by_day[dt.date()].add(dt.hour)
            local.append((h, dt))
        busiest = max((len(s) for s in hours_by_day.values()), default=0)
        for bid, limit in (("marathon", 18), ("insomnia", 22)):
            at = next((end_ts(d) for d in sorted(hours_by_day) if len(hours_by_day[d]) >= limit), None)
            put(bid, busiest >= limit, at, min(busiest, limit), limit)

        # streaks and comebacks
        active = sorted(days)
        best_run, cur_run, prev = 0, 0, None
        reached = {}
        comeback = None
        for d in active:
            if prev is not None and (d - prev).days > 14 and comeback is None:
                comeback = end_ts(d)
            cur_run = cur_run + 1 if prev is not None and (d - prev).days == 1 else 1
            best_run = max(best_run, cur_run)
            for n in (3, 7, 14, 30, 100):
                if cur_run >= n:
                    reached.setdefault(n, end_ts(d))
            prev = d
        for n in (3, 7, 14, 30, 100):
            put("streak_%d" % n, best_run >= n, reached.get(n), min(best_run, n), n)
        put("comeback", comeback is not None, comeback)

        # time of day
        def first(pred):
            return next((h for h, dt in local if pred(dt)), None)
        def nth_day(pred, n):
            seen = set()
            for h, dt in local:
                if pred(dt):
                    seen.add(dt.date())
                    if len(seen) >= n:
                        return h, len(seen)
            return None, len(seen)
        x = first(lambda dt: dt.hour < 4)
        put("night_owl", x is not None, x)
        x = first(lambda dt: 4 <= dt.hour < 6)
        put("early_bird", x is not None, x)
        at, n = nth_day(lambda dt: dt.hour >= 21, 25)
        put("overtime", at is not None, at, min(n, 25), 25)
        at, n = nth_day(lambda dt: dt.hour == 12, 20)
        put("lunch_desk", at is not None, at, min(n, 20), 20)
        at, n = nth_day(lambda dt: dt.weekday() == 0 and dt.hour < 8, 4)
        put("monday_spirit", at is not None, at, min(n, 4), 4)
        at, n = nth_day(lambda dt: dt.weekday() == 4 and dt.hour >= 22, 3)
        put("friday_deploy", at is not None, at, min(n, 3), 3)
        x = first(lambda dt: dt.day == dt.month)
        put("twin_date", x is not None, x)
        anniv = None
        if info_.get("created_at"):
            j = datetime.fromtimestamp(info_["created_at"], tz).date()
            anniv = next((h for h, dt in local if dt.date() > j and (dt.month, dt.day) == (j.month, j.day)), None)
        put("anniversary", anniv is not None, anniv)
        x = first(lambda dt: (dt.month == 12 and dt.day == 31 and dt.hour == 23) or (dt.month == 1 and dt.day == 1 and dt.hour == 0))
        put("new_year", x is not None, x)
        dayset = set(days)
        weekends = [end_ts(d + timedelta(days=1)) for d in sorted(dayset) if d.weekday() == 5 and d + timedelta(days=1) in dayset]
        put("weekend_warrior", len(weekends) >= 4, weekends[3] if len(weekends) >= 4 else None, min(len(weekends), 4), 4)
        weeks = defaultdict(set)
        for d in dayset:
            weeks[d - timedelta(days=d.weekday())].add(d.weekday())
        full = next((end_ts(w, 7) for w in sorted(weeks) if len(weeks[w]) == 7), None)
        put("seven_days", full is not None, full, max((len(s) for s in weeks.values()), default=0), 7)
        week_hours = defaultdict(set)
        clock = None
        for h, dt in local:
            wk = dt.date() - timedelta(days=dt.weekday())
            week_hours[wk].add(dt.hour)
            if len(week_hours[wk]) == 24 and clock is None:
                clock = h
        put("around_the_clock", clock is not None, clock, max((len(v) for v in week_hours.values()), default=0), 24)
        months = defaultdict(dict)
        for d, v in days.items():
            months[(d.year, d.month)][d] = v
        sprint = None
        for ym in sorted(months):
            if ym >= this_month or len(months[ym]) < 10:
                continue
            nxt = datetime(ym[0] + (ym[1] == 12), ym[1] % 12 + 1, 1).date()
            last = nxt - timedelta(days=1)
            if last in months[ym] and months[ym][last] >= max(months[ym].values()):
                sprint = end_ts(last)
                break
        put("month_end_sprint", sprint is not None, sprint)
        joined = info_.get("created_at")
        jd = datetime.fromtimestamp(joined, tz).date() if joined else None
        put("same_day", jd is not None and jd in dayset, end_ts(jd) if jd else None)

        # models
        fam = defaultdict(float)
        free = 0.0
        for m, v in mods.items():
            f = family(m)
            if f:
                fam[f] += v
            if "free" in m.lower():
                free += v
        put("haiku_poet", fam["haiku"] >= M, None, min(fam["haiku"], M), M)
        put("sonnet_master", fam["sonnet"] >= 25 * M, None, min(fam["sonnet"], 25 * M), 25 * M)
        put("magnum_opus", fam["opus"] >= 25 * M, None, min(fam["opus"], 25 * M), 25 * M)
        used = sum(1 for f in ("haiku", "sonnet", "opus") if fam[f] > 0)
        put("trilogy", used == 3, None, used, 3)
        distinct = sum(1 for v in mods.values() if v > 0)
        put("explorer", distinct >= 5, None, min(distinct, 5), 5)
        top = max(mods.values(), default=0)
        put("loyal", total_out >= M and top >= 0.95 * total_out)
        put("free_rider", free >= M, None, min(free, M), M)

        # cache and input
        cr, inp, cc = tot.get("cache_read", 0), tot.get("input", 0), tot.get("cache_creation", 0)
        for bid, limit in (("cache_king", 1000 * M), ("elephant", 10000 * M), ("alexandria", 25000 * M)):
            put(bid, cr >= limit, None, min(cr, limit), limit)
        put("context_architect", cc >= 100 * M, None, min(cc, 100 * M), 100 * M)
        put("bookworm", inp >= 100 * M, None, min(inp, 100 * M), 100 * M)

        # cost, sessions, time
        cost = tot.get("cost", 0)
        for bid, limit in (("pricey_coffee", 100), ("sultan", 1000), ("whale", 10000)):
            put(bid, cost >= limit, None, round(min(cost, limit), 2), limit)
        ses = tot.get("sessions", 0)
        for bid, limit in (("hundred_sessions", 100), ("thousand_sessions", 1000), ("elite", 1337)):
            put(bid, ses >= limit, None, int(min(ses, limit)), limit)
        hrs = tot.get("active", 0) / 3600
        put("first_flight", hrs >= 10, None, int(min(hrs, 10)), 10)
        put("flight_hours", hrs >= 100, None, int(min(hrs, 100)), 100)

        # setup
        mach = machines.get(e, {})
        put("photogenic", info_.get("avatar"))
        put("two_machines", mach.get("computers", 0) >= 2, None, min(mach.get("computers", 0), 2), 2)
        put("back_roads", mach.get("router"))
        req = tot.get("requests", 0)
        put("router_rider", req >= 1000, None, int(min(req, 1000)), 1000)

        # standings
        put("daily_champion", e in first_day_win, first_day_win.get(e))
        put("weekly_podium", e in first_podium, first_podium.get(e))
        put("weekly_king", e in first_week_win, first_week_win.get(e))
        put("monthly_champion", e in first_month_win, first_month_win.get(e))
        put("unbeatable", e in unbeatable, unbeatable.get(e))
        put("backbone", e in backbone, backbone.get(e))
        put("rising_star", e in rising, rising.get(e))
        put("first_wave", e in first_wave)
        put("dynasty", e in dynasty, dynasty.get(e))
        put("dark_horse", e in dark_horse, dark_horse.get(e))
        n = len(first_in.get(e, []))
        put("first_in", n >= 10, first_in[e][9] if n >= 10 else None, min(n, 10), 10)
        n = len(last_out.get(e, []))
        put("last_out", n >= 10, last_out[e][9] if n >= 10 else None, min(n, 10), 10)
        n = len(in_sync.get(e, []))
        put("in_sync", n >= 10, in_sync[e][9] if n >= 10 else None, min(n, 10), 10)

        others = sum(1 for k, v in r.items() if v["done"])
        put("collector", others >= 40, None, min(others, 40), 40)
        out[e] = r
    return out


def evaluate_fresh(emps, hourly, hour_models, overtakes, since, tz, now):
    """Season badges: like evaluate(), but only usage from `since` (unix time, per badge) counts.
    hourly: {id: [(hour, output)]}; hour_models: {id: [(hour, model, output)]}; overtakes: {id: [ts]}."""
    today = datetime.fromtimestamp(now, tz).date()
    out = {}
    start = {b: since.get(b, now) for b in FRESH}
    first = min(start.values())

    def day_of(h):
        return datetime.fromtimestamp(h, tz).date()

    def end_ts(d):
        return int(datetime.combine(d + timedelta(days=1), datetime.min.time(), tz).timestamp()) - 1

    def from_day(b):
        return day_of(start[b]) if start[b] < now else today + timedelta(days=1)

    # per person: day -> output, day -> set of hours, day -> {model: output}; everyone: day -> {person: output}
    days, hours_of, models_of = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(set)), defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    board, cum = defaultdict(dict), defaultdict(lambda: defaultdict(dict))   # cum: day -> hour -> {person: running total}
    for e, rows in hourly.items():
        for h, v in rows:
            if v <= 0 or h < first:
                continue
            d = day_of(h)
            days[e][d] += v
            hours_of[e][d].add(datetime.fromtimestamp(h, tz).hour)
            board[d][e] = board[d].get(e, 0) + v
    for e, rows in hour_models.items():
        for h, m, v in rows:
            if v > 0 and h >= first:
                models_of[e][day_of(h)][m] += v
    for d in board:
        acc = defaultdict(float)
        per_hour = defaultdict(dict)
        for e, rows in hourly.items():
            for h, v in rows:
                if v > 0 and h >= first and day_of(h) == d:
                    per_hour[h][e] = per_hour[h].get(e, 0) + v
        for h in sorted(per_hour):
            for e, v in per_hour[h].items():
                acc[e] += v
            cum[d][h] = dict(acc)

    def standings(d):
        return sorted(((v, e) for e, v in board[d].items() if v > 0), reverse=True)

    done_days = sorted(d for d in board if d < today)

    for e, info_ in emps.items():
        r = {}

        def put(bid, ok, at=None, cur=None, target=None):
            r[bid] = {"done": bool(ok), "at": at if ok else None, "cur": cur, "target": target}

        def mine(b):   # this person's days from the badge's start
            return {d: v for d, v in days[e].items() if d >= from_day(b)}

        # season opener
        md = mine("season_opener")
        put("season_opener", bool(md), end_ts(min(md)) if md else None)

        # runs of days over a floor
        def run_badge(bid, floor, need):
            md, best, run, prev, at = mine(bid), 0, 0, None, None
            for d in sorted(md):
                run = run + 1 if prev is not None and (d - prev).days == 1 and md[d] >= floor else (1 if md[d] >= floor else 0)
                prev = d if md[d] >= floor else None
                best = max(best, run)
                if run >= need and at is None:
                    at = end_ts(d)
            put(bid, at is not None, at, min(best, need), need)
        run_badge("clockwork", 100_000, 14)
        run_badge("diesel", 500_000, 7)

        # three shifts
        n, at = 0, None
        for d in sorted(hours_of[e]):
            if d < from_day("three_shifts"):
                continue
            hs = hours_of[e][d]
            if any(6 <= x < 12 for x in hs) and any(12 <= x < 18 for x in hs) and any(x >= 18 for x in hs):
                n += 1
                if n == 5:
                    at = end_ts(d)
        put("three_shifts", n >= 5, at, min(n, 5), 5)

        # juggler, opus marathon
        jd = [d for d in sorted(models_of[e]) if d >= from_day("juggler") and sum(1 for v in models_of[e][d].values() if v > 0) >= 4]
        most = max((sum(1 for v in ms.values() if v > 0) for d, ms in models_of[e].items() if d >= from_day("juggler")), default=0)
        put("juggler", bool(jd), end_ts(jd[0]) if jd else None, min(most, 4), 4)
        opus = {d: sum(v for m, v in ms.items() if "opus" in m.lower()) for d, ms in models_of[e].items() if d >= from_day("opus_marathon")}
        od = [d for d in sorted(opus) if opus[d] >= 5_000_000]
        put("opus_marathon", bool(od), end_ts(od[0]) if od else None, min(max(opus.values(), default=0), 5_000_000), 5_000_000)

        # big hours and nights
        hrs = [(h, v) for h, v in hourly.get(e, []) if h >= start["supernova"]]
        big = max((v for _, v in hrs), default=0)
        put("supernova", big >= 3_000_000, next((h for h, v in sorted(hrs) if v >= 3_000_000), None), min(big, 3_000_000), 3_000_000)
        nights = defaultdict(float)
        for h, v in hourly.get(e, []):
            if h >= start["night_racer"] and v > 0 and datetime.fromtimestamp(h, tz).hour < 5:
                nights[day_of(h)] += v
        nd = [d for d in sorted(nights) if nights[d] >= 1_000_000]
        put("night_racer", bool(nd), end_ts(nd[0]) if nd else None, min(max(nights.values(), default=0), 1_000_000), 1_000_000)

        # quantum leap: needs 7 earlier days inside the season too
        md, at = mine("quantum_leap"), None
        for d in sorted(md):
            prev7 = [md.get(d - timedelta(days=k), 0) for k in range(1, 8)]
            if all((d - timedelta(days=k)) >= from_day("quantum_leap") for k in range(1, 8)):
                avg = sum(prev7) / 7
                if avg >= 100_000 and md[d] >= 5 * avg:
                    at = end_ts(d)
                    break
        put("quantum_leap", at is not None, at)

        # a month of 100M
        months = defaultdict(float)
        for d, v in mine("hundred_month").items():
            months[(d.year, d.month)] += v
        hm = max(months.values(), default=0)
        put("hundred_month", hm >= 100_000_000, end_ts(today) if hm >= 100_000_000 else None, min(hm, 100_000_000), 100_000_000)

        # overtakes
        ov = sorted(t for t in overtakes.get(e, []) if t >= start["overtaker"])
        put("overtaker", len(ov) >= 10, ov[9] if len(ov) >= 10 else None, min(len(ov), 10), 10)

        # standings on completed days
        wins = [d for d in done_days if standings(d) and standings(d)[0][1] == e]
        def first_win(bid, ok):
            for d in wins:
                if d >= from_day(bid) and ok(d):
                    return end_ts(d)
            return None
        at = first_win("weekend_champion", lambda d: d.weekday() >= 5)
        put("weekend_champion", at is not None, at)
        at = first_win("domination", lambda d: len(standings(d)) >= 3 and standings(d)[0][0] > standings(d)[1][0] + standings(d)[2][0])
        put("domination", at is not None, at)
        at = first_win("photo_finish", lambda d: len(standings(d)) >= 2 and standings(d)[0][0] - standings(d)[1][0] < 0.01 * standings(d)[0][0])
        put("photo_finish", at is not None, at)

        def rock(d):
            for h in sorted(cum[d]):
                active = sorted(((v, p) for p, v in cum[d][h].items() if v > 0))
                if len(active) >= 4 and active[0][1] == e:
                    return True
            return False
        at = first_win("rock_bottom", rock)
        put("rock_bottom", at is not None, at)

        def streak_of(bid, cond, need):
            run, prev, best, at = 0, None, 0, None
            for d in done_days:
                if d < from_day(bid):
                    continue
                ok = cond(d)
                run = run + 1 if ok and prev is not None and (d - prev).days == 1 else (1 if ok else 0)
                prev = d if ok else None
                best = max(best, run)
                if run >= need and at is None:
                    at = end_ts(d)
            put(bid, at is not None, at, min(best, need), need)
        streak_of("hattrick", lambda d: d in wins, 3)
        streak_of("podium_regular", lambda d: e in [p for _, p in standings(d)[:3]], 10)

        # team spirit: every teammate (3+) active the same day
        team = info_.get("team")
        mates = [p for p, i in emps.items() if team and i.get("team") == team]
        n, at = 0, None
        if len(mates) >= 3:
            for d in sorted(board):
                if d >= from_day("team_spirit") and all(board[d].get(p, 0) > 0 for p in mates):
                    n += 1
                    if n == 5:
                        at = end_ts(d)
        put("team_spirit", n >= 5, at, min(n, 5), 5)
        out[e] = r
    return out


def merge(base, fresh):
    """Add the season results and count the collector again with them."""
    for e, r in fresh.items():
        base.setdefault(e, {}).update(r)
    for e, r in base.items():
        others = sum(1 for k, v in r.items() if v["done"] and k != "collector")
        r["collector"] = {"done": others >= 40, "at": None, "cur": min(others, 40), "target": 40}
    return base


# One line of flavour per badge, shown big in the badge popup (English, Indonesian).
FLAVOR = {
    "first_words": ("Every legend starts with a single token.", "Setiap legenda dimulai dari satu token."),
    "hundred_k": ("A hundred thousand words. That is a whole novel.", "Seratus ribu kata. Itu sudah setebal novel."),
    "millionaire": ("Your first million. Worth framing.", "Juta pertamamu. Pantas dipajang."),
    "ten_million": ("The factory runs at full steam.", "Pabriknya sudah jalan dengan kecepatan penuh."),
    "fifty_million": ("Gravity can no longer hold you.", "Gravitasi sudah tak sanggup menahanmu."),
    "hundred_million": ("A number that used to live only in dreams.", "Angka yang dulu cuma ada di mimpi."),
    "half_billion": ("They will tell stories about this one.", "Kisah tentangmu akan diceritakan turun-temurun."),
    "billion": ("They said it could not be done. They were wrong.", "Katanya mustahil. Ternyata tidak."),
    "lightning_day": ("One day, one million. Lightning fast.", "Satu hari, satu juta. Secepat kilat."),
    "token_storm": ("The forecast said cloudy. You brought a storm.", "Ramalan cuaca bilang berawan. Kamu datang membawa badai."),
    "hurricane": ("Category five. Evacuate the servers.", "Kategori lima. Server harap mengungsi."),
    "rush_hour": ("Sixty minutes that felt like a week.", "Enam puluh menit yang terasa seperti seminggu."),
    "million_hour": ("The reactor is past its safe limits.", "Reaktornya sudah lewat batas aman."),
    "marathon": ("Eighteen hours. Your keyboard needs a rest.", "Delapan belas jam. Keyboard-mu butuh istirahat."),
    "insomnia": ("Sleep is a suggestion, not a rule.", "Tidur itu saran, bukan kewajiban."),
    "streak_3": ("A spark turning into a habit.", "Percikan kecil yang mulai jadi kebiasaan."),
    "streak_7": ("Seven days. The fire is lit.", "Tujuh hari. Apinya sudah menyala."),
    "streak_14": ("Two weeks without missing a beat.", "Dua minggu tanpa absen sekali pun."),
    "streak_30": ("A month of discipline. Respect.", "Sebulan penuh disiplin. Salut."),
    "streak_100": ("One hundred days. A volcano never takes a day off.", "Seratus hari. Gunung berapi tidak pernah libur."),
    "comeback": ("You went away. You came back stronger.", "Sempat menghilang, kembali lebih kuat."),
    "night_owl": ("While the city sleeps, your code is wide awake.", "Saat kota tertidur, kodemu justru terjaga."),
    "early_bird": ("Up before the sun, and before the bugs.", "Bangun sebelum matahari, juga sebelum bug-nya."),
    "overtime": ("The office lights go off. Your screen stays on.", "Lampu kantor padam, layarmu tetap menyala."),
    "lunch_desk": ("Lunch in one hand, a pull request in the other.", "Nasi bungkus di satu tangan, pull request di tangan lain."),
    "monday_spirit": ("Mondays are afraid of you.", "Hari Senin yang takut sama kamu."),
    "weekend_warrior": ("Weekends are just days without meetings.", "Akhir pekan cuma hari tanpa rapat."),
    "seven_days": ("Monday to Sunday, no exceptions.", "Senin sampai Minggu, tanpa pengecualian."),
    "around_the_clock": ("Every hour on the clock has seen you work.", "Setiap jam di jam dinding pernah melihatmu bekerja."),
    "month_end_sprint": ("Saved the best for last.", "Yang terbaik disimpan untuk akhir."),
    "friday_deploy": ("Deploying on a Friday night. Fearless, or reckless?", "Deploy Jumat malam. Pemberani, atau nekat?"),
    "twin_date": ("A pretty date deserves pretty code.", "Tanggal cantik, kodenya ikut cantik."),
    "anniversary": ("One year together. Happy anniversary.", "Setahun bersama. Selamat ulang tahun."),
    "new_year": ("The fireworks were nice. The code was nicer.", "Kembang apinya indah, kodenya lebih indah."),
    "same_day": ("Installed it and got straight to work.", "Baru pasang, langsung tancap gas."),
    "haiku_poet": ("Short and swift / a million tokens later / the poem is done.", "Singkat dan cepat / sejuta token kemudian / puisinya jadi."),
    "sonnet_master": ("Fourteen lines at a time, twenty-five million tokens long.", "Empat belas baris sekali tulis, dua puluh lima juta token panjangnya."),
    "magnum_opus": ("Your great work, written with the greatest model.", "Mahakaryamu, ditulis bersama model terbesar."),
    "trilogy": ("Haiku, Sonnet, Opus. The full collection.", "Haiku, Sonnet, Opus. Koleksinya lengkap."),
    "explorer": ("Five models, five worlds explored.", "Lima model, lima dunia dijelajahi."),
    "loyal": ("One model, and no other.", "Satu model, tiada yang lain."),
    "free_rider": ("The best things in life are free.", "Hal terbaik dalam hidup itu gratis."),
    "cache_king": ("Why write it twice when you can remember it?", "Buat apa ditulis dua kali kalau bisa diingat?"),
    "elephant": ("Ten billion tokens remembered. Nothing forgotten.", "Sepuluh miliar token diingat. Tak satu pun terlupa."),
    "alexandria": ("The great library of the ancient world, rebuilt in cache.", "Perpustakaan terbesar zaman kuno, dibangun ulang di cache."),
    "context_architect": ("You do not just write prompts. You design contexts.", "Kamu tidak sekadar menulis prompt. Kamu merancang konteks."),
    "bookworm": ("A hundred million tokens read, and still hungry.", "Seratus juta token dibaca, masih lapar juga."),
    "pricey_coffee": ("That is a lot of coffee, in API money.", "Kalau dirupakan kopi, sudah bergelas-gelas."),
    "sultan": ("Spending like royalty.", "Gaya belanja ala sultan."),
    "whale": ("The ocean makes way for you.", "Lautan pun memberi jalan untukmu."),
    "hundred_sessions": ("A hundred times you said: let's begin.", "Seratus kali kamu bilang: ayo mulai."),
    "thousand_sessions": ("A thousand sessions, a thousand stories.", "Seribu sesi, seribu cerita."),
    "elite": ("1337. Only the elite will get it.", "1337. Hanya yang elite yang paham."),
    "first_flight": ("Ten hours in the air. The pilot licence is close.", "Sepuluh jam terbang. Lisensi pilot sudah dekat."),
    "flight_hours": ("A hundred hours. Captain on deck.", "Seratus jam terbang. Kapten di anjungan."),
    "photogenic": ("Now the leaderboard knows your face.", "Sekarang leaderboard kenal wajahmu."),
    "two_machines": ("One laptop is never enough.", "Satu laptop tidak pernah cukup."),
    "back_roads": ("Not the highway. Your own route.", "Bukan jalan tol, tapi jalur sendiri."),
    "router_rider": ("A thousand requests through the router.", "Seribu request melintasi router."),
    "daily_champion": ("For one whole day, nobody did more.", "Selama sehari penuh, tidak ada yang mengalahkanmu."),
    "weekly_podium": ("Up on the podium. The view is good from here.", "Naik podium. Pemandangannya indah dari atas."),
    "weekly_king": ("Seven days on the throne.", "Tujuh hari duduk di singgasana."),
    "monthly_champion": ("A whole month at the top. A true champion.", "Sebulan penuh di puncak. Juara sejati."),
    "unbeatable": ("Seven days, seven wins. Nobody came close.", "Tujuh hari, tujuh kemenangan. Tidak ada yang mendekat."),
    "dynasty": ("Four weeks, one name at the top. A dynasty is born.", "Empat minggu, satu nama di puncak. Sebuah dinasti lahir."),
    "dark_horse": ("Nobody saw you coming.", "Tidak ada yang menyangka kamu datang."),
    "first_in": ("The first light of the day is yours.", "Cahaya pertama hari ini milikmu."),
    "last_out": ("The last one out turns off the lights.", "Yang terakhir pulang, yang mematikan lampu."),
    "in_sync": ("Same hour, same rhythm, same team.", "Jam yang sama, irama yang sama, tim yang sama."),
    "backbone": ("Half the team's work rests on your shoulders.", "Separuh beban tim ada di pundakmu."),
    "rising_star": ("Just arrived, already shining.", "Baru datang, sudah bersinar."),
    "first_wave": ("You were here from the very start.", "Kamu sudah ada sejak awal."),
    "collector": ("Forty badges. The trophy cabinet is full.", "Empat puluh lencana. Lemari pialamu sudah penuh."),
    "season_opener": ("A new season, and you were there on day one.", "Musim baru, dan kamu sudah hadir sejak hari pertama."),
    "clockwork": ("Tick, tock, a hundred thousand. Every single day.", "Tik, tok, seratus ribu. Setiap hari, tanpa absen."),
    "three_shifts": ("Morning, afternoon and night. You covered every shift.", "Pagi, siang, dan malam. Semua shift kamu yang jaga."),
    "juggler": ("Four models in the air, and none of them dropped.", "Empat model di udara, tak satu pun jatuh."),
    "weekend_champion": ("Everyone was resting. You were winning.", "Semua orang libur. Kamu justru juara."),
    "team_spirit": ("The whole team showed up, together.", "Satu tim hadir semua, kompak."),
    "diesel": ("Slow to start, impossible to stop.", "Lambat panas, tapi tak bisa dihentikan."),
    "hattrick": ("Three days, three wins. The crowd goes wild.", "Tiga hari, tiga kemenangan. Penonton bersorak."),
    "domination": ("Second and third together still could not catch you.", "Peringkat dua dan tiga digabung pun tak sanggup mengejar."),
    "photo_finish": ("Decided by a whisker. The camera saw you first.", "Ditentukan setipis rambut. Kamera melihatmu lebih dulu."),
    "night_racer": ("Empty streets, full throttle.", "Jalanan sepi, gas pol."),
    "quantum_leap": ("Yesterday's ceiling is today's floor.", "Atap kemarin jadi lantai hari ini."),
    "overtaker": ("Ten times, someone saw you in the mirror and then you were gone.", "Sepuluh kali kamu terlihat di spion, lalu menghilang ke depan."),
    "supernova": ("Three million tokens in an hour. Visible from space.", "Tiga juta token dalam sejam. Terlihat dari luar angkasa."),
    "opus_marathon": ("Five million words with the grandest model, in a single day.", "Lima juta token bersama model termegah, dalam sehari."),
    "rock_bottom": ("Last place in the morning, champion by night.", "Pagi di posisi buncit, malamnya jadi juara."),
    "hundred_month": ("One month, one hundred million. A legendary month.", "Satu bulan, seratus juta. Bulan yang melegenda."),
    "podium_regular": ("Ten days on the podium. They should name a step after you.", "Sepuluh hari di podium. Anak tangganya pantas diberi namamu."),
}
