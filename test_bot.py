import unittest
import os
import json
import time
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
            "createchannel", "blockrole", "unblockrole", "renamerole", "serverinfo", "userinfo", "add", "coupons",
            "optimize", "calc", "cart", "undo", "remove", "clear", "checkout", "run-stress-test",
            "testadd", "testcoupons", "testoptimize", "testcart", "testcheckout", "testclear",
            "savings", "history", "delete-last-trip", "setup", "permit", "ping", "about",
            "lockdown", "filter", "modlogs", "case", "note",
            "blackjack", "connect4", "trivia", "slots", "rps", "coinflip", "roll",
            "cvsaccount", "accounts", "cliphelp", "setauth"
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
            "createchannel": "manage_channels",
            "blockrole": "manage_channels",
            "unblockrole": "manage_channels",
            "renamerole": "manage_roles",
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
            "setup": "is_owner",
            "permit": "is_owner",
        }
        
        for cmd_name, expected_check in mod_commands.items():
            cmd = main.bot.get_command(cmd_name)
            self.assertIsNotNone(cmd, f"Command '{cmd_name}' not found!")
            self.assertTrue(len(cmd.checks) > 0, f"Command '{cmd_name}' has no permission checks!")

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
        case_id = main.log_mod_case(1234, "Warn", "User1", "Mod1", "Test Reason")
        self.assertIsInstance(case_id, int)
        
        main.add_mod_note(1234, 5678, "Mod1", "Suspicious account")
        notes = main.get_mod_notes(1234, 5678)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["note"], "Suspicious account")
        cleared = main.clear_mod_notes(1234, 5678)
        self.assertEqual(cleared, 1)

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

    def test_cvs_dob_auth_url(self):
        test_acc = {"id": 99, "xid": "vUrxGYnes", "birthday": "2000-09-09"}
        url = main.build_cvs_dob_auth_url(test_acc, "/deals/coupons")
        self.assertIsNotNone(url)
        self.assertIn("account-auth/dob", url)
        self.assertIn("xid=vUrxGYnes", url)
        self.assertIn("fURL=%2Fdeals%2Fcoupons", url)

        embed, file = main.format_account_card(test_acc)
        self.assertIn("1-Click DOB Login", embed.description)
        self.assertIn("09/09/2000", embed.description)


if __name__ == '__main__':
    unittest.main()




