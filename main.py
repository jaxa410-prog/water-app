async def send_summary_report(target_message: types.Message):
    try:
        today_date = datetime.now(UZ_TIMEZONE).strftime("%Y-%m-%d")
        reports = load_reports()
        if not reports:
            await target_message.answer("📭 База отчетов пуста.")
            return

        courier_stats = {}
        matched = 0

        for r in reports:
            if str(r.get("date", "")).startswith(today_date):
                matched += 1
                cid = str(r.get("courier_id", "неизвестно"))
                cname = str(r.get("courier_name", "Курьер"))
                client_id = str(r.get("client_id", "Не указан"))

                if cid not in courier_stats:
                    courier_stats[cid] = {"name": cname, "deliv": 0, "ret": 0, "rev": 0, "earn": 0, "orders": 0, "clients": []}

                deliv = int(r.get("delivered", 0) or 0)
                ret = int(r.get("returned", 0) or 0)
                rev = int(r.get("total_price", 0) or 0)
                earn = int(r.get("earnings", 0) or 0)

                courier_stats[cid]["deliv"] += deliv
                courier_stats[cid]["ret"] += ret
                courier_stats[cid]["rev"] += rev
                courier_stats[cid]["earn"] += earn
                courier_stats[cid]["orders"] += 1
                courier_stats[cid]["clients"].append(f"• {client_id} (сдал: {deliv}, пустых: {ret})")

        if matched == 0:
            await target_message.answer(f"📭 За сегодня отчетов нет.")
            return

        text = f"📊 СВОДНЫЙ ОТЧЕТ ЗА СЕГОДНЯ ({today_date}):\n\n"
        tot_deliv, tot_ret, tot_rev = 0, 0, 0

        for cid, s in courier_stats.items():
            tot_deliv += s["deliv"]
            tot_ret += s["ret"]
            tot_rev += s["rev"]
            
            rev_formatted = f"{s['rev']:,}".replace(",", " ")
            
            text += f"🚚 Курьер: {s['name']}\n"
            text += f"   ▫️ Выполнено заказов: {s['orders']}\n"
            text += f"   ▫️ Сдано воды: {s['deliv']} шт | Пустых: {s['ret']} шт\n"
            text += f"   ▫️ Выручка: {rev_formatted} сум\n"
            text += "   📍 Точки доставки:\n"
            for c_line in s["clients"]:
                text += f"      {c_line}\n"
            text += "\n"

        tot_rev_formatted = f"{tot_rev:,}".replace(",", " ")
        text += f"💧 Всего сдали воды: {tot_deliv} шт\n"
        text += f"🔄 Всего собрали пустых: {tot_ret} шт\n"
        text += f"💰 Общая выручка: {tot_rev_formatted} сум"
        
        await target_message.answer(text)
    except Exception as e:
        await target_message.answer(f"⚠️ Ошибка: {e}")
