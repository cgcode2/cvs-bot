import unittest
import os
import json
import time
import copy
import asyncio
from datetime import datetime, timedelta

import main

class TestAIOBot(unittest.TestCase):

    def test_calculate_best_bundles_empty(self):
        due, bundles = main.calculate_best_bundles([], [])
        self.assertEqual(due, 0.0)
        due, bundles = main.calculate_best_bundles([{"name": "shampoo", "price": 10.0}], [])
        self.assertEqual(due, 10.0)

    def test_calculate_best_bundles_dollar_coupons(self):
        items = [
            {"name": "item1", "price": 4.0},
            {"name": "item2", "price": 6.0},
            {"name": "item3", "price": 5.0},
            {"name": "item4", "price": 3.0},
        ]
        coupons = [8.0, 5.0]
        due, bundles = main.calculate_best_bundles(items, coupons)
        self.assertAlmostEqual(due, 5.0, places=2)

    def test_calculate_best_bundles_half_off(self):
        items = [
            {"name": "expensive", "price": 20.0},
            {"name": "cheap", "price": 4.0},
        ]
        coupons = ["half"]
        due, bundles = main.calculate_best_bundles(items, coupons)
        self.assertAlmostEqual(due, 14.0, places=2)

    def test_calculate_best_bundles_performance(self):
        items = [{"name": f"item{i}", "price": round(2.0 + (i * 1.3), 2)} for i in range(14)]
        coupons = [10.0, 8.0, 5.0, "half"]
        start_time = time.time()
        due, bundles = main.calculate_best_bundles(items, coupons)
        duration = time.time() - start_time
        self.assertLess(duration, 0.5, f"Optimization took too long: {duration:.4f}s")
        self.assertGreater(due, 0.0)

    def test_multi_user_session_isolation(self):
        user1 = 11111
        user2 = 22222
        
        session1 = main.get_session(user1)
        session2 = main.get_session(user2)
        
        session1["items"].append({"name": "apple", "price": 2.0})
        session2["items"].append({"name": "banana", "price": 3.0})
        
        self.assertEqual(len(main.get_session(user1)["items"]), 1)
        self.assertEqual(main.get_session(user1)["items"][0]["name"], "apple")
        
        self.assertEqual(len(main.get_session(user2)["items"]), 1)
        self.assertEqual(main.get_session(user2)["items"][0]["name"], "banana")
        
        main.reset_session(user1)
        self.assertEqual(len(main.get_session(user1)["items"]), 0)
        self.assertEqual(len(main.get_session(user2)["items"]), 1)

    def test_date_parsing(self):
        d1 = main._parse_history_date("2026-07-01")
        self.assertIsNotNone(d1)
        self.assertEqual(d1.year, 2026)
        self.assertEqual(d1.month, 7)
        self.assertEqual(d1.day, 1)

        d2 = main._parse_history_date("07/01/2026")
        self.assertEqual(d1, d2)

        self.assertIsNone(main._parse_history_date("invalid-date"))
        self.assertIsNone(main._parse_history_date(None))

    def test_duration_parsing(self):
        self.assertEqual(main.parse_duration("30s"), timedelta(seconds=30))
        self.assertEqual(main.parse_duration("10m"), timedelta(minutes=10))
        self.assertEqual(main.parse_duration("2h"), timedelta(hours=2))
        self.assertEqual(main.parse_duration("1d"), timedelta(days=1))
        self.assertEqual(main.parse_duration("2w"), timedelta(weeks=2))
        self.assertIsNone(main.parse_duration("invalid"))
        self.assertIsNone(main.parse_duration(None))

    def test_resolve_color(self):
        c1 = main.resolve_color("red")
        self.assertIsNotNone(c1)
        c2 = main.resolve_color("#ff0000")
        self.assertIsNotNone(c2)
        c3 = main.resolve_color("unknown_color_xyz")
        self.assertIsNone(c3)

    def test_coupon_costs(self):
        self.assertEqual(main.coupon_cost(8.0), 1.00)
        self.assertEqual(main.coupon_cost("half"), 0.01)
        self.assertEqual(main.coupon_label("half"), "50% Off One Item")
        self.assertEqual(main.coupon_label(8.0), "$8.00 Off")

    def test_interactive_views_instantiation(self):
        panel = main.QuickCartActionView(12345)  # OptimizerPanelView was removed (merged into QuickCartActionView)
        self.assertIsNotNone(panel)
        mod_panel = main.ModerationPanelView()
        self.assertIsNotNone(mod_panel)
        quick_view = main.QuickCartActionView(12345)
        self.assertIsNotNone(quick_view)
        self.assertIsNotNone(main.TicketLaunchView())
        self.assertIsNotNone(main.TicketControlView())
        self.assertIsNotNone(main.TicketCloseConfirmView())
        self.assertIsNotNone(main.FormatServerConfirmView(12345))
        self.assertIsNotNone(main.DeleteChannelsConfirmView(12345))
        self.assertIsNotNone(main.FoodAccountPurchaseView())
        self.assertIsNotNone(main.CouponHubLaunchView())
        self.assertIsNotNone(main.CouponRoomControlView(12345))
        self.assertIsNotNone(main.StaffModPanelButtonView())
        hub_embed = main.build_coupon_hub_embed()
        self.assertIn("Coupon Optimizer", hub_embed.title)
        mod_embed = main.build_staff_modpanel_embed()
        self.assertIn("Staff Control Center", mod_embed.title)
        food_embed = main.build_food_accounts_embed()
        self.assertIn("Taco Bell", food_embed.fields[0].name)
        self.assertIn("Pizza Hut", food_embed.fields[1].name)
        self.assertIn("$10.00", food_embed.fields[0].value)
        self.assertIn("$15.00", food_embed.fields[1].value)

    def test_smart_items_parser(self):
        # Multi-word items with commas
        res1 = main.parse_items_input("Fairlife Whole Milk $4.49, Crest 3D White Toothpaste 6.99")
        self.assertEqual(len(res1), 2)
        self.assertEqual(res1[0]["name"], "Fairlife Whole Milk")
        self.assertEqual(res1[0]["price"], 4.49)
        self.assertEqual(res1[1]["name"], "Crest 3D White Toothpaste")
        self.assertEqual(res1[1]["price"], 6.99)

        # Multi-line items
        res2 = main.parse_items_input("Shampoo 5.99\nTide Pods 12.50\nSoap 1.25")
        self.assertEqual(len(res2), 3)

    def test_smart_coupons_parser(self):
        res1 = main.parse_coupons_input("8, 8, 5, 50% off, half, $10")
        self.assertEqual(res1, [8.0, 8.0, 5.0, "half", "half", 10.0])

    def test_build_strategy_embed(self):
        items = [{"name": "Item A", "price": 10.0}, {"name": "Item B", "price": 6.0}]
        coupons = [8.0]
        embed = main.build_strategy_embed(items, coupons)
        self.assertIn("Step-by-Step", embed.title)
        self.assertEqual(len(embed.fields), 3)

    def test_all_help_commands_registered(self):
        registered_commands = set(main.bot.all_commands.keys())
        expected_commands = [
            "panel", "modpanel", "help", "embed", "nukechannel", "purge", "kick", "ban", "unban",
            "timeout", "untimeout", "warn", "warnings", "clearwarnings", "lock", "unlock", "slowmode",
            "serverinfo", "userinfo", "add", "coupons",
            "optimize", "calc", "cart", "undo", "remove", "clear", "checkout", "run-stress-test",
            "savings", "history", "delete-last-trip", "setup", "permit", "ping", "about",
            "lockdown", "filter", "modlogs", "case", "note",
            "blackjack", "connect4", "trivia", "slots", "rps", "coinflip", "roll",
            "balance", "daily", "pay", "leaderboard",
            "formatserver", "deletechannels", "ticketpanel", "say", "foodpanel",
            "revoke", "paid", "complete", "otp", "deliver", "claim", "close",
            "invoice", "orderstats", "clearorder", "fixroles", "resetchannel"
        ]
        for cmd in expected_commands:
            self.assertIn(cmd, registered_commands, f"Command '{cmd}' is missing from bot registration!")

    def test_delete_last_trip(self):
        original_savings = dict(main.savings_tracker)
        original_trips = list(main.savings_tracker.get("trips", []))
        
        try:
            items = [{"name": "Item A", "price": 10.0}]
            coupons = [8.0]
            embed, subtotal, total_due, coupon_spend, net_saved, now = main._do_checkout(items, coupons)
            
            initial_count = main.savings_tracker["trip_count"]
            initial_net_saved = main.savings_tracker["total_net_saved"]
            
            removed = main.delete_last_trip()
            self.assertIsNotNone(removed)
            self.assertEqual(main.savings_tracker["trip_count"], initial_count - 1)
            self.assertAlmostEqual(main.savings_tracker["total_net_saved"], initial_net_saved - net_saved, places=2)
        finally:
            main.savings_tracker.clear()
            main.savings_tracker.update(original_savings)
            main.savings_tracker["trips"] = original_trips
            main.save_savings(main.savings_tracker)

    def test_moderation_permissions(self):
        mod_commands = {
            "modpanel": "manage_channels",
            "embed": "manage_messages",
            "nukechannel": "manage_channels",
            "purge": "manage_messages",
            "kick": "kick_members",
            "ban": "ban_members",
            "unban": "ban_members",
            "timeout": "moderate_members",
            "untimeout": "moderate_members",
            "warn": "manage_messages",
            "warnings": "manage_messages",
            "clearwarnings": "administrator",
            "lock": "manage_channels",
            "unlock": "manage_channels",
            "slowmode": "manage_channels",
            "lockdown": "administrator",
            "filter": "manage_guild",
            "modlogs": "manage_messages",
            "case": "manage_messages",
            "note": "manage_messages",
            "setup": "manage_channels",
            "permit": "manage_channels",
            "revoke": "manage_channels",
            "paid": "manage_messages",
            "complete": "manage_messages",
            "otp": "manage_messages",
            "deliver": "manage_messages",
            "claim": "manage_messages",
            "close": "manage_messages",
            "run-stress-test": "is_owner",
            "delete-last-trip": "is_owner",
            "accounts": "is_owner",
        }
        
        for cmd_name, expected_check in mod_commands.items():
            cmd = main.bot.get_command(cmd_name)
            self.assertIsNotNone(cmd, f"Command '{cmd_name}' not found!")
            self.assertTrue(len(cmd.checks) > 0, f"Command '{cmd_name}' has no permission checks!")
            if expected_check == "is_owner":
                # Verify slash command is hidden from non-admins in autocomplete dropdown
                self.assertIsNotNone(cmd.app_command.default_permissions, f"Command '{cmd_name}' missing default_permissions!")
                self.assertTrue(cmd.app_command.default_permissions.administrator, f"Command '{cmd_name}' default_permissions.administrator must be True!")

        # Ensure public shopping, games, and utility commands are accessible without is_owner
        public_commands = [
            "add", "coupons", "optimize", "calc", "cart", "undo", "remove", "clear",
            "checkout", "savings", "history", "panel", "balance", "daily", "pay",
            "leaderboard", "blackjack", "connect4", "trivia", "slots", "rps", "coinflip", "roll"
        ]
        for p_cmd in public_commands:
            cmd = main.bot.get_command(p_cmd)
            self.assertIsNotNone(cmd, f"Public command '{p_cmd}' not found!")
            check_names = [getattr(c, '__qualname__', str(c)) for c in cmd.checks]
            self.assertFalse(any("is_owner" in c for c in check_names), f"Public command '{p_cmd}' should NOT have is_owner check!")

    def test_blackjack_hand_calculations(self):
        self.assertEqual(main.calculate_hand_value(["10♠", "K♥"]), 20)
        self.assertEqual(main.calculate_hand_value(["A♠", "9♥"]), 20)
        self.assertEqual(main.calculate_hand_value(["A♠", "A♥", "9♦"]), 21) # 11 + 1 + 9 = 21
        self.assertEqual(main.calculate_hand_value(["10♠", "8♥", "5♦"]), 23) # Bust
        deck = main.create_shuffled_deck()
        self.assertEqual(len(deck), 52)

    def test_connect4_win_detection(self):
        board = main.create_connect4_board()
        self.assertFalse(main.check_connect4_win(board, "🔴"))
        for col in range(4):
            main.drop_piece(board, col, "🔴")
        self.assertTrue(main.check_connect4_win(board, "🔴"))

        b2 = main.create_connect4_board()
        for _ in range(4):
            main.drop_piece(b2, 0, "🟡")
        self.assertTrue(main.check_connect4_win(b2, "🟡"))

    def test_automod_word_filter(self):
        test_gid = 999999
        main.add_filter_word(test_gid, "badword")
        words = main.get_filter_words(test_gid)
        self.assertIn("badword", words)
        main.remove_filter_word(test_gid, "badword")
        self.assertNotIn("badword", main.get_filter_words(test_gid))

    def test_mod_cases_and_notes(self):
        original_cases = copy.deepcopy(main.mod_cases_db)
        try:
            case_id = main.log_mod_case(1234, "Warn", "User1", "Mod1", "Test Reason")
            self.assertIsInstance(case_id, int)
            
            main.add_mod_note(1234, 5678, "Mod1", "Suspicious account")
            notes = main.get_mod_notes(1234, 5678)
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]["note"], "Suspicious account")
            cleared = main.clear_mod_notes(1234, 5678)
            self.assertEqual(cleared, 1)
        finally:
            main.mod_cases_db.clear()
            main.mod_cases_db.update(original_cases)
            main.save_mod_cases(main.mod_cases_db)

    def test_barcode_generation(self):
        buf = main.generate_code128_barcode_bytes("48443912049281")
        self.assertIsNotNone(buf)
        buf_val = buf.getvalue()
        self.assertTrue(len(buf_val) > 100)
        self.assertEqual(buf_val[:8], b'\x89PNG\r\n\x1a\n') # Valid PNG magic header

    def test_cvs_accounts_db(self):
        self.assertEqual(len(main.cvs_accounts_db), 21)
        acc1 = main.get_cvs_account("1")
        self.assertIsNotNone(acc1)
        self.assertEqual(acc1["name"], "Andrew Bartlett")
        self.assertEqual(acc1["extraCareNumber"], "4767093294360")
        
        acc_name = main.get_cvs_account("Murphy")
        self.assertIsNotNone(acc_name)
        self.assertEqual(acc_name["id"], 2)

    def test_format_account_card_links(self):
        acc = main.cvs_accounts_db[0]
        embed, file = main.format_account_card(acc)
        self.assertIn("Open Deals & Rewards (Send to Card)", embed.description)
        self.assertIn("https://www.cvs.com/extracare/deals-and-rewards", embed.description)
        self.assertIn("cvs_barcode.png", file.filename)

    def test_economy_system(self):
        original_economy = copy_db = dict(main.economy_db)
        test_uid1 = 88888801
        test_uid2 = 88888802

        try:
            # Clean test keys
            main.economy_db.pop(str(test_uid1), None)
            main.economy_db.pop(str(test_uid2), None)

            # Initial default balance
            bal1 = main.get_user_coins(test_uid1)
            self.assertEqual(bal1, main.DEFAULT_STARTING_COINS)

            # Add coins
            main.add_user_coins(test_uid1, 500)
            self.assertEqual(main.get_user_coins(test_uid1), 1500)

            # Deduct coins (success)
            deducted = main.deduct_user_coins(test_uid1, 300)
            self.assertTrue(deducted)
            self.assertEqual(main.get_user_coins(test_uid1), 1200)

            # Deduct coins (insufficient funds)
            deducted_fail = main.deduct_user_coins(test_uid1, 50000)
            self.assertFalse(deducted_fail)
            self.assertEqual(main.get_user_coins(test_uid1), 1200)

            # Daily claim (first time)
            success, reward, rem = main.claim_daily_coins(test_uid1)
            self.assertTrue(success)
            self.assertEqual(reward, main.DAILY_REWARD_COINS)
            self.assertEqual(main.get_user_coins(test_uid1), 1450)

            # Daily claim (on cooldown)
            success2, current_bal, rem2 = main.claim_daily_coins(test_uid1)
            self.assertFalse(success2)
            self.assertIsNotNone(rem2)
            self.assertGreater(rem2, 0)

            # Transfer coins
            t_success, t_msg = main.transfer_user_coins(test_uid1, test_uid2, 200)
            self.assertTrue(t_success)
            self.assertEqual(main.get_user_coins(test_uid1), 1250)
            self.assertEqual(main.get_user_coins(test_uid2), main.DEFAULT_STARTING_COINS + 200)

            # Leaderboard
            board = main.get_coin_leaderboard(limit=5)
            self.assertIsInstance(board, list)
            self.assertGreater(len(board), 0)
        finally:
            main.economy_db.clear()
            main.economy_db.update(original_economy)
            main.save_economy(main.economy_db)

    def test_3x3_slots_randomness_and_generation(self):
        grid = main.roll_3x3_slots()
        self.assertEqual(len(grid), 3)
        for row in grid:
            self.assertEqual(len(row), 3)
            for cell in row:
                self.assertIn(cell, main.SLOT_SYMBOLS)

        # Statistical distribution test over multiple rolls
        counts = {s: 0 for s in main.SLOT_SYMBOLS}
        total_cells = 0
        for _ in range(200):
            g = main.roll_3x3_slots()
            for r in g:
                for c in r:
                    counts[c] += 1
                    total_cells += 1

        self.assertEqual(total_cells, 200 * 9)
        for s, count in counts.items():
            self.assertGreater(count, 0, f"Symbol {s} was never rolled")

    def test_3x3_slots_evaluations(self):
        stake = 100

        # Center row jackpot: 777
        grid_jackpot = [
            ["🍒", "🍋", "⭐"],
            ["7️⃣", "7️⃣", "7️⃣"],
            ["💎", "🔔", "🍇"]
        ]
        winnings, hits, title = main.evaluate_3x3_slots(grid_jackpot, stake)
        self.assertEqual(winnings, int(100 * 50.0))
        self.assertIn("Center Row", hits[0])

        # Diagonal win
        grid_diag = [
            ["💎", "🍋", "⭐"],
            ["🍒", "💎", "🔔"],
            ["🍇", "🍒", "💎"]
        ]
        winnings_d, hits_d, title_d = main.evaluate_3x3_slots(grid_diag, stake)
        self.assertEqual(winnings_d, int(100 * 25.0))
        self.assertTrue(any("Diagonal ↘" in h for h in hits_d))

        # Full Board 9x jackpot
        grid_full = [["7️⃣"] * 3 for _ in range(3)]
        winnings_f, hits_f, title_f = main.evaluate_3x3_slots(grid_full, stake)
        self.assertEqual(winnings_f, int(100 * 100.0))
        self.assertIn("FULL BOARD", title_f)

        # Grid format helper
        fmt_spinning = main.format_3x3_grid(grid_jackpot, active_cols=0)
        self.assertIn("🌀", fmt_spinning)
        fmt_done = main.format_3x3_grid(grid_jackpot, active_cols=3)
        self.assertNotIn("🌀", fmt_done)
        self.assertIn("7️⃣", fmt_done)

    def test_cvs_pagination_view(self):
        view = main.CVSAccountsPaginationView(current_idx=0)
        self.assertEqual(view.current_idx, 0)
        self.assertTrue(any(item.label == "Previous" for item in view.children if hasattr(item, "label")))
        self.assertTrue(any(item.label == "Next" for item in view.children if hasattr(item, "label")))
        self.assertTrue(any(item.label == "Custom Barcode" for item in view.children if hasattr(item, "label")))
        self.assertTrue(any(isinstance(item, main.AccountSelectDropdown) for item in view.children))

        # Check next and prev index wrap-around logic
        view.current_idx = (view.current_idx + 1) % len(main.cvs_accounts_db)
        self.assertEqual(view.current_idx, 1)
        view.update_select()
        self.assertEqual(view.current_idx, 1)

        view.current_idx = (view.current_idx - 1) % len(main.cvs_accounts_db)
        self.assertEqual(view.current_idx, 0)
        view.update_select()
        self.assertEqual(view.current_idx, 0)

    def test_is_protected_channel(self):
        class MockChannel:
            def __init__(self, name, category=None):
                self.name = name
                self.category = category

        # Direct name checks
        self.assertTrue(main.is_protected_channel("form-automation"))
        self.assertTrue(main.is_protected_channel("#form-automation"))
        self.assertTrue(main.is_protected_channel("form_automation"))
        self.assertTrue(main.is_protected_channel("FORM-AUTOMATION"))
        self.assertTrue(main.is_protected_channel("Form Automation"))
        self.assertTrue(main.is_protected_channel(MockChannel("form-automation")))
        self.assertTrue(main.is_protected_channel(MockChannel("form_automation")))

        # Category inheritance check
        cat = MockChannel("form-automation")
        child_ch = MockChannel("random-subchannel", category=cat)
        self.assertTrue(main.is_protected_channel(child_ch))

        # Unprotected checks
        self.assertFalse(main.is_protected_channel("general-chat"))
        self.assertFalse(main.is_protected_channel("aio-coupon-optimizer"))
        self.assertFalse(main.is_protected_channel(MockChannel("general-chat")))
        self.assertFalse(main.is_protected_channel(None))

    def test_ticket_system_operations(self):
        original_tickets = copy.deepcopy(main.tickets_db)
        try:
            main.tickets_db.clear()
            main.tickets_db.update({"counter": 0, "tickets": {}})

            # Create ticket record
            rec = main.create_ticket_record(guild_id=123, channel_id=999, owner_id=456, channel_name="ticket-0001-test")
            self.assertEqual(rec["id"], 1)
            self.assertEqual(rec["status"], "open")
            self.assertIsNone(rec["claimed_by"])

            # Check active ticket
            active_id = main.get_user_active_ticket(guild_id=123, user_id=456)
            self.assertEqual(active_id, 999)
            self.assertIsNone(main.get_user_active_ticket(guild_id=123, user_id=789))

            # Claim ticket
            claimed = main.claim_ticket_record(channel_id=999, staff_id=888)
            self.assertTrue(claimed)
            self.assertEqual(main.tickets_db["tickets"]["999"]["claimed_by"], 888)

            # Close ticket
            closed = main.close_ticket_record(channel_id=999)
            self.assertTrue(closed)
            self.assertEqual(main.tickets_db["tickets"]["999"]["status"], "closed")
            self.assertIsNone(main.get_user_active_ticket(guild_id=123, user_id=456))

            # Test transcript formatting
            class MockMessage:
                def __init__(self, author_name, content, created_at):
                    self.author = author_name
                    self.display_name = author_name
                    self.clean_content = content
                    self.created_at = created_at
                    self.attachments = []

            now = datetime(2026, 9, 10, 14, 0, 0)
            msgs = [
                MockMessage("Alice", "Hello I need help with my coupons!", now),
                MockMessage("BobStaff", "Sure! I can help you with that.", now)
            ]
            transcript = main.format_ticket_transcript(msgs, ticket_id=1, owner_id=456)
            self.assertIn("AIO BOT TICKET TRANSCRIPT", transcript)
            self.assertIn("Ticket Number: #0001", transcript)
            self.assertIn("Hello I need help with my coupons!", transcript)
            self.assertIn("Sure! I can help you with that.", transcript)
        finally:
            main.tickets_db.clear()
            main.tickets_db.update(original_tickets)
            main.save_tickets()

    def test_say_command_helper(self):
        class MockChannel:
            def __init__(self):
                self.sent_messages = []

            async def send(self, content=None, files=None):
                self.sent_messages.append({"content": content, "files": files})

        channel = MockChannel()
        res1 = asyncio.run(main._do_say(channel, "", []))
        self.assertFalse(res1)
        self.assertEqual(len(channel.sent_messages), 0)

        res2 = asyncio.run(main._do_say(channel, "Hello world repost!", []))
        self.assertTrue(res2)
        self.assertEqual(len(channel.sent_messages), 1)
        self.assertEqual(channel.sent_messages[0]["content"], "Hello world repost!")

    def test_food_accounts_embed_content(self):
        embed = main.build_food_accounts_embed()
        self.assertIn("Taco Bell", embed.fields[0].name)
        self.assertIn("15 off your entire order", embed.fields[0].value)
        self.assertIn("Free Chalupa Supreme", embed.fields[0].value)
        self.assertIn("Pizza Hut", embed.fields[1].name)
        self.assertIn("2 Large pizzas", embed.fields[1].value)
        self.assertIn("Triple Chocolate Fudge Brownie", embed.fields[1].value)
        self.assertIn("Open a ticket", embed.fields[0].value)
        self.assertIn("Send Code", embed.fields[0].value)

        modal = main.FoodAccountOrderModal(brand="Taco Bell", price=10.0)
        self.assertEqual(modal.brand, "Taco Bell")
        self.assertEqual(modal.price, 10.0)

    def test_blueprint_preservation_helpers(self):
        cat_names = main.get_blueprint_category_names()
        ch_names = main.get_blueprint_channel_names()

        self.assertIn("📌 INFORMATION", cat_names)
        self.assertIn("📁 TICKETS", cat_names)
        self.assertIn("📢-announcements", ch_names)
        self.assertIn("🛒-coupon-optimizer", ch_names)
        self.assertIn("🌮🍕-food-rewards", ch_names)

        class MockCategory:
            def __init__(self, name):
                self.name = name

        class MockChannel:
            def __init__(self, name, category=None):
                self.name = name
                self.category = category

        # Protected channel is preserved in all modes
        protected_ch = MockChannel("form-automation")
        self.assertTrue(main.is_preserved_channel(protected_ch, mode="clean_old"))
        self.assertTrue(main.is_preserved_channel(protected_ch, mode="wipe_all"))

        # Channel inside protected category is preserved in all modes
        protected_nested = MockChannel("submission-logs", category=MockCategory("Form Automation"))
        self.assertTrue(main.is_preserved_channel(protected_nested, mode="clean_old"))
        self.assertTrue(main.is_preserved_channel(protected_nested, mode="wipe_all"))

        # Default/old channel is NOT preserved
        old_ch = MockChannel("general")
        self.assertFalse(main.is_preserved_channel(old_ch, mode="clean_old"))
        self.assertFalse(main.is_preserved_channel(old_ch, mode="wipe_all"))

        # Blueprint channel is preserved in clean_old, but not in wipe_all
        bp_ch = MockChannel("📢-announcements")
        self.assertTrue(main.is_preserved_channel(bp_ch, mode="clean_old"))
        self.assertFalse(main.is_preserved_channel(bp_ch, mode="wipe_all"))

        # Ticket channel is preserved in clean_old
        ticket_ch = MockChannel("ticket-0001-cody")
        self.assertTrue(main.is_preserved_channel(ticket_ch, mode="clean_old"))
        self.assertFalse(main.is_preserved_channel(ticket_ch, mode="wipe_all"))

    def test_channel_delete_interactive_view(self):
        class MockGuild:
            def __init__(self, channels):
                self.channels = channels
            def get_channel(self, cid):
                for c in self.channels:
                    if getattr(c, 'id', None) == cid:
                        return c
                return None

        class MockChannel:
            def __init__(self, cid, name, is_cat=False, is_vc=False):
                self.id = cid
                self.name = name
                self.position = 0
                self.is_cat = is_cat
                self.is_vc = is_vc
                self.category = None

        channels = [
            MockChannel(101, "form-automation"),  # strictly protected
            MockChannel(102, "general"),          # old/unformatted
            MockChannel(103, "General Voice", is_vc=True), # old voice
            MockChannel(104, "📢-announcements"), # blueprint
        ]
        mock_guild = MockGuild(channels)

        view = main.ChannelDeleteInteractiveView(author_id=12345, guild=mock_guild, page=0)
        self.assertEqual(view.author_id, 12345)
        deletable = view._get_deletable_channels()
        deletable_names = [c.name for c in deletable]
        self.assertNotIn("form-automation", deletable_names)
        self.assertIn("general", deletable_names)
        self.assertIn("General Voice", deletable_names)
        self.assertIn("📢-announcements", deletable_names)

        # Check embed output
        embed = view.build_embed()
        self.assertIn("Channel Deletion & Management Suite", embed.title)
        self.assertIn("#form-automation", embed.description)
        self.assertIn("Leftover/Old Channels Detected", embed.description)

        # Check buttons presence
        button_labels = [item.label for item in view.children if isinstance(item, main.discord.ui.Button)]
        self.assertTrue(any("Delete Selected" in l for l in button_labels))
        self.assertIn("Delete All Old Channels", button_labels)
        self.assertIn("Wipe All Channels", button_labels)
        self.assertIn("Cancel", button_labels)

    def test_private_cvs_and_permit_helpers(self):
        cat_names = main.get_blueprint_category_names()
        self.assertIn("🔒 PRIVATE CVS", cat_names)
        self.assertIn("🛍️ SAVINGS & REWARDS", cat_names)

        class MockRole:
            def __init__(self, name):
                self.name = name

        class MockMember:
            def __init__(self, roles=None, manage_channels=False, admin=False):
                self.roles = roles or []
                self.guild_permissions = main.discord.Permissions(administrator=admin, manage_channels=manage_channels)

        staff_member = MockMember(roles=[MockRole("Staff")])
        self.assertTrue(main.is_staff_or_admin(staff_member))

        mod_member = MockMember(roles=[MockRole("Moderator")])
        self.assertTrue(main.is_staff_or_admin(mod_member))

        admin_perm_member = MockMember(admin=True)
        self.assertTrue(main.is_staff_or_admin(admin_perm_member))

        regular_member = MockMember(roles=[MockRole("Member")])
        self.assertFalse(main.is_staff_or_admin(regular_member))

        # Test find_cvs_optimizer_channel
        class MockTextChannel:
            def __init__(self, name):
                self.name = name
                self.category = None

        class MockGuildWithChannels:
            def __init__(self, text_channels):
                self.text_channels = text_channels
                self.categories = []

        guild1 = MockGuildWithChannels([MockTextChannel("🛒-coupon-optimizer")])
        found = main.find_cvs_optimizer_channel(guild1)
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "🛒-coupon-optimizer")

        guild2 = MockGuildWithChannels([MockTextChannel("aio-coupon-optimizer")])
        found2 = main.find_cvs_optimizer_channel(guild2)
        self.assertIsNotNone(found2)
        self.assertEqual(found2.name, "aio-coupon-optimizer")

    def test_founder_role_detection(self):
        class MockRole:
            def __init__(self, name: str):
                self.name = name

        class MockGuild:
            def __init__(self, roles):
                self.roles = roles

        # Guild with "Founder" role
        g1 = MockGuild([MockRole("Member"), MockRole("Founder")])
        f1 = main.get_founder_role(g1)
        self.assertIsNotNone(f1)
        self.assertEqual(f1.name, "Founder")

        # Guild with "Founders" role
        g2 = MockGuild([MockRole("Staff"), MockRole("Founders")])
        f2 = main.get_founder_role(g2)
        self.assertIsNotNone(f2)
        self.assertEqual(f2.name, "Founders")

        # Guild with "Owner" role
        g3 = MockGuild([MockRole("Admin"), MockRole("Owner")])
        f3 = main.get_founder_role(g3)
        self.assertIsNotNone(f3)
        self.assertEqual(f3.name, "Owner")

        # Guild with role containing "founder"
        g4 = MockGuild([MockRole("Co-Founder & CEO")])
        f4 = main.get_founder_role(g4)
        self.assertIsNotNone(f4)
        self.assertEqual(f4.name, "Co-Founder & CEO")

        # Guild without founder role
        g5 = MockGuild([MockRole("Member"), MockRole("VIP")])
        f5 = main.get_founder_role(g5)
        self.assertIsNone(f5)

        # None guild
        self.assertIsNone(main.get_founder_role(None))

    def test_moderator_and_staff_role_helpers(self):
        class MockRole:
            def __init__(self, name: str):
                self.name = name

        class MockGuild:
            def __init__(self, roles):
                self.roles = roles

        class MockMember:
            def __init__(self, roles):
                self.roles = roles
                self.guild_permissions = main.discord.Permissions(administrator=False, manage_channels=False, manage_messages=False)

        # Moderator role detection
        g1 = MockGuild([MockRole("Member"), MockRole("Moderator")])
        m1 = main.get_moderator_role(g1)
        self.assertIsNotNone(m1)
        self.assertEqual(m1.name, "Moderator")

        # Mod role detection
        g2 = MockGuild([MockRole("Mod")])
        m2 = main.get_moderator_role(g2)
        self.assertIsNotNone(m2)
        self.assertEqual(m2.name, "Mod")

        # Staff role detection
        g3 = MockGuild([MockRole("Staff")])
        s1 = main.get_staff_role(g3)
        self.assertIsNotNone(s1)
        self.assertEqual(s1.name, "Staff")

        # is_staff_or_admin check for Moderator / Mod / Mods
        mem_mod = MockMember([MockRole("Moderator")])
        self.assertTrue(main.is_staff_or_admin(mem_mod))

        mem_short_mod = MockMember([MockRole("Mod")])
        self.assertTrue(main.is_staff_or_admin(mem_short_mod))

        mem_mods = MockMember([MockRole("Mods")])
        self.assertTrue(main.is_staff_or_admin(mem_mods))

        mem_regular = MockMember([MockRole("Member"), MockRole("Gamer")])
        self.assertFalse(main.is_staff_or_admin(mem_regular))

    def test_order_fulfillment_flow_and_ticket_status(self):
        saved_db = copy.deepcopy(main.tickets_db)
        try:
            # Create a test ticket
            record = main.create_ticket_record(guild_id=123, channel_id=98765, owner_id=456, channel_name="order-tacobell-0001")
            self.assertEqual(record["status"], "open")
            self.assertEqual(main.tickets_db["tickets"]["98765"]["status"], "open")

            # Mark Paid
            res_paid = main.update_ticket_status(98765, "paid")
            self.assertTrue(res_paid)
            self.assertEqual(main.tickets_db["tickets"]["98765"]["status"], "paid")

            # Mark Completed
            res_complete = main.update_ticket_status(98765, "completed")
            self.assertTrue(res_complete)
            self.assertEqual(main.tickets_db["tickets"]["98765"]["status"], "completed")

            # Non-existent ticket returns False
            self.assertFalse(main.update_ticket_status(99999999, "paid"))
        finally:
            main.tickets_db.clear()
            main.tickets_db.update(saved_db)
            main.save_tickets()

    def test_ticket_control_view_fulfillment_buttons(self):
        view = main.TicketControlView()
        custom_ids = [getattr(child, "custom_id", None) for child in view.children]
        
        # Verify panel buttons exist (Claim, Transcript, Close)
        self.assertIn("aio_ticket_claim_btn", custom_ids)
        self.assertIn("aio_ticket_transcript_btn", custom_ids)
        self.assertIn("aio_ticket_close_btn", custom_ids)
        # Paid and Complete were removed from panel per user request (now commands only)
        self.assertNotIn("aio_ticket_mark_paid_btn", custom_ids)
        self.assertNotIn("aio_ticket_mark_complete_btn", custom_ids)

        # Verify /paid and /complete are registered bot commands
        self.assertIn("paid", main.bot.all_commands)
        self.assertIn("complete", main.bot.all_commands)

    def test_order_stats_tracker_and_clearorder(self):
        saved_db = copy.deepcopy(main.tickets_db)
        try:
            main.tickets_db["completed_orders"] = []
            
            # Record test order 1
            o1 = main.record_completed_order(
                guild_id=101,
                ticket_id=1,
                channel_id=1001,
                channel_name="order-tacobell-0001",
                customer_id=501,
                customer_name="Customer1",
                completed_by_id=901,
                completed_by_name="Staff1",
                brand="Taco Bell",
                amount=10.0,
                notes="Paid via CashApp"
            )
            self.assertEqual(o1["order_id"], 1)
            self.assertEqual(len(main.tickets_db["completed_orders"]), 1)

            # Record test order 2
            o2 = main.record_completed_order(
                guild_id=101,
                ticket_id=2,
                channel_id=1002,
                channel_name="order-pizzahut-0002",
                customer_id=502,
                customer_name="Customer2",
                completed_by_id=901,
                completed_by_name="Staff1",
                brand="Pizza Hut",
                amount=15.0
            )
            self.assertEqual(o2["order_id"], 2)
            self.assertEqual(len(main.tickets_db["completed_orders"]), 2)

            # Remove specific order (test order 1)
            removed = main.remove_completed_order(1)
            self.assertIsNotNone(removed)
            self.assertEqual(removed["order_id"], 1)
            self.assertEqual(len(main.tickets_db["completed_orders"]), 1)
            self.assertEqual(main.tickets_db["completed_orders"][0]["order_id"], 2)

            # Clear all remaining orders
            cleared = main.clear_completed_orders(guild_id=101)
            self.assertEqual(cleared, 1)
            self.assertEqual(len(main.tickets_db["completed_orders"]), 0)
        finally:
            main.tickets_db.clear()
            main.tickets_db.update(saved_db)
            main.save_tickets()

    def test_get_channel_mention_helper(self):
        class MockChannel:
            def __init__(self, name: str, cid: int):
                self.name = name
                self.id = cid
            @property
            def mention(self):
                return f"<#{self.id}>"

        class MockGuild:
            def __init__(self, channels):
                self.channels = channels

        ch1 = MockChannel("🧾-receipt-brags", 111222)
        ch2 = MockChannel("form-automation", 333444)
        guild = MockGuild([ch1, ch2])

        # Exact match
        self.assertEqual(main.get_channel_mention(guild, "🧾-receipt-brags"), "<#111222>")
        # Partial match without prefix
        self.assertEqual(main.get_channel_mention(guild, "receipt-brags"), "<#111222>")
        # Channel not in guild returns fallback
        self.assertEqual(main.get_channel_mention(guild, "nonexistent", fallback="#default"), "#default")
        # None guild returns fallback
        self.assertEqual(main.get_channel_mention(None, "anything", fallback="#fallback"), "#fallback")

    def test_staff_payment_profiles(self):
        saved_db = dict(main.staff_payment_db)
        try:
            main.save_staff_payment(12345, cashapp="$MyCashtag", venmo="@MyVenmo")
            prof = main.get_staff_payment(12345)
            self.assertEqual(prof.get("cashapp"), "MyCashtag")
            self.assertEqual(prof.get("venmo"), "MyVenmo")
        finally:
            main.staff_payment_db.clear()
            main.staff_payment_db.update(saved_db)

    def test_refresh_channel_content_and_protection(self):
        import asyncio

        class MockChannel:
            def __init__(self, name: str):
                self.name = name
                self.messages_sent = []
                self.purged = False

            async def purge(self, limit=100):
                self.purged = True

            async def send(self, content=None, embed=None, view=None, **kwargs):
                self.messages_sent.append({"content": content, "embed": embed, "view": view})

            async def edit(self, **kwargs):
                pass

        # 1. Protected channel form-automation cannot be reset (raises ValueError)
        protected_ch = MockChannel("form-automation")
        with self.assertRaises(ValueError):
            asyncio.run(main.refresh_channel_content(protected_ch, author_id=123, clear_history=True))
        self.assertFalse(protected_ch.purged)

        # 2. Food rewards channel deploys food accounts embed and FoodAccountPurchaseView
        food_ch = MockChannel("🌮🍕-food-rewards")
        res_food = asyncio.run(main.refresh_channel_content(food_ch, author_id=123, clear_history=True))
        self.assertTrue(food_ch.purged)
        self.assertIn("Fast Food Rewards", res_food)
        self.assertEqual(len(food_ch.messages_sent), 1)
        self.assertIsInstance(food_ch.messages_sent[0]["view"], main.FoodAccountPurchaseView)
        self.assertIn("Fast Food Preloaded", food_ch.messages_sent[0]["embed"].title)

        # 3. Ticket channel deploys TicketLaunchView
        ticket_ch = MockChannel("📩-open-a-ticket")
        res_ticket = asyncio.run(main.refresh_channel_content(ticket_ch, author_id=123, clear_history=True))
        self.assertTrue(ticket_ch.purged)
        self.assertIn("Support", res_ticket)
        self.assertEqual(len(ticket_ch.messages_sent), 1)
        self.assertIsInstance(ticket_ch.messages_sent[0]["view"], main.TicketLaunchView)

        # 4. Coupon optimizer channel deploys CouponHubLaunchView
        opt_ch = MockChannel("🛒-coupon-optimizer")
        res_opt = asyncio.run(main.refresh_channel_content(opt_ch, author_id=123, clear_history=True))
        self.assertTrue(opt_ch.purged)
        self.assertIn("Coupon Optimizer Hub", res_opt)
        self.assertEqual(len(opt_ch.messages_sent), 1)
        self.assertIsInstance(opt_ch.messages_sent[0]["view"], main.CouponHubLaunchView)

        # 5. Mod panel channel deploys StaffModPanelButtonView
        mod_ch = MockChannel("🎛️-mod-panel")
        res_mod = asyncio.run(main.refresh_channel_content(mod_ch, author_id=123, clear_history=True))
        self.assertTrue(mod_ch.purged)
        self.assertIn("Staff Control Center", res_mod)
        self.assertEqual(len(mod_ch.messages_sent), 1)
        self.assertIsInstance(mod_ch.messages_sent[0]["view"], main.StaffModPanelButtonView)

    def test_is_staff_member_and_resolve_member(self):
        class MockRole:
            def __init__(self, name: str):
                self.name = name

        class MockMember:
            def __init__(self, uid: int, name: str, display_name: str, roles=None, is_owner=False, is_admin=False):
                self.id = uid
                self.name = name
                self.display_name = display_name
                self.roles = roles or []
                self.guild = None
                self._is_owner = is_owner
                self._is_admin = is_admin

            @property
            def guild_permissions(self):
                class MockPerms:
                    def __init__(self, is_admin):
                        self.administrator = is_admin
                        self.manage_channels = is_admin
                        self.manage_messages = is_admin
                return MockPerms(self._is_admin)

        class MockGuild:
            def __init__(self, owner_id: int):
                self.id = 9999
                self.owner_id = owner_id
                self.members = []

            def get_member(self, uid: int):
                for m in self.members:
                    if m.id == uid:
                        return m
                return None

        guild = MockGuild(owner_id=100)
        m_owner = MockMember(100, "owneruser", "Server Owner", is_owner=True)
        m_owner.guild = guild
        m_mod = MockMember(101, "moduser", "Moderator Mike", roles=[MockRole("Moderator")])
        m_mod.guild = guild
        m_regular = MockMember(102, "regularguy", "Regular Guy", roles=[MockRole("Member")])
        m_regular.guild = guild
        guild.members = [m_owner, m_mod, m_regular]

        self.assertTrue(main.is_staff_member(m_owner))
        self.assertTrue(main.is_staff_member(m_mod))
        self.assertFalse(main.is_staff_member(m_regular))
        self.assertFalse(main.is_staff_member(None))

        # Test resolve_member_from_input
        self.assertEqual(main.resolve_member_from_input(guild, "<@100>"), m_owner)
        self.assertEqual(main.resolve_member_from_input(guild, "101"), m_mod)
        self.assertEqual(main.resolve_member_from_input(guild, "regularguy"), m_regular)
        self.assertEqual(main.resolve_member_from_input(guild, "Mike"), m_mod)
        self.assertIsNone(main.resolve_member_from_input(guild, "nonexistent"))

    def test_coupon_room_control_view_and_hub_embed(self):
        view = main.CouponRoomControlView(room_owner_id=555)
        self.assertEqual(view.room_owner_id, 555)

        # Check all 7 buttons exist on view
        labels = [item.label for item in view.children if hasattr(item, "label")]
        self.assertIn("Add Items", labels)
        self.assertIn("Load Coupons", labels)
        self.assertIn("Optimize Plan", labels)
        self.assertIn("Undo Last", labels)
        self.assertIn("Checkout", labels)
        self.assertIn("Clear Cart", labels)
        self.assertIn("Close Room", labels)

        hub = main.CouponHubLaunchView()
        hub_labels = [item.label for item in hub.children if hasattr(item, "label")]
        self.assertIn("Open Private Optimizer Room", hub_labels)

    def test_staff_modpanel_view_and_embed(self):
        view = main.StaffModPanelButtonView()
        labels = [item.label for item in view.children if hasattr(item, "label")]

        # Discipline buttons
        self.assertIn("Warn", labels)
        self.assertIn("Timeout", labels)
        self.assertIn("Kick", labels)
        self.assertIn("Ban", labels)
        self.assertIn("Purge", labels)

        # Channel & Server buttons
        self.assertIn("Lock Channel", labels)
        self.assertIn("Unlock Channel", labels)
        self.assertIn("Slowmode", labels)
        self.assertIn("Server Lockdown", labels)

        # Store, Billing & Hierarchy buttons
        self.assertIn("Create Invoice", labels)
        self.assertIn("Order Stats", labels)
        self.assertIn("Fix Roles", labels)
        self.assertIn("Refresh Store", labels)

        # Panels & Info
        self.assertIn("Refresh Tickets", labels)
        self.assertIn("Refresh Hub", labels)
        self.assertIn("Server Info", labels)

        embed = main.build_staff_modpanel_embed()
        self.assertIn("Staff Control Center", embed.title)
        self.assertIn("Member Discipline", embed.description)
        self.assertIn("Channel & Server Security", embed.description)

    def test_dedicated_setup_commands(self):
        cmd_names = [c.name for c in main.bot.commands]
        self.assertIn("setup", cmd_names)
        self.assertIn("setup-mod-panel", cmd_names)
        self.assertIn("setup-all-features", cmd_names)
        self.assertIn("setup-food-store", cmd_names)
        self.assertIn("setup-tickets", cmd_names)
        self.assertIn("sync-commands", cmd_names)

        setup_cmd = main.bot.get_command("setup")
        self.assertIsNotNone(setup_cmd)
        self.assertIn("setup-coupon-hub", setup_cmd.aliases)

        mod_panel_cmd = main.bot.get_command("setup-mod-panel")
        self.assertIsNotNone(mod_panel_cmd)
        self.assertIn("setupmodpanel", mod_panel_cmd.aliases)

        all_feat_cmd = main.bot.get_command("setup-all-features")
        self.assertIsNotNone(all_feat_cmd)
        self.assertIn("setupfeatures", all_feat_cmd.aliases)


if __name__ == '__main__':
    unittest.main()




