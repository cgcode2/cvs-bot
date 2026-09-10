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
            "accounts", "cvsaccount",
            "formatserver", "deletechannels", "ticketpanel", "say", "foodpanel",
            "revoke"
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
        self.assertIn("$15 off your entire order", embed.fields[0].value)
        self.assertIn("Free Chalupa Supreme", embed.fields[0].value)
        self.assertIn("Pizza Hut", embed.fields[1].name)
        self.assertIn("2 Large pizzas", embed.fields[1].value)
        self.assertIn("Triple chocolate fudge brownie", embed.fields[1].value)

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


if __name__ == '__main__':
    unittest.main()




