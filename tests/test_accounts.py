import base64
import hashlib
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from noteworthy_app.accounts import (
    COOKIE_NAME,
    SECURE_COOKIE_NAME,
    AccountError,
    Accounts,
    FileAccountStore,
    accounts_from_env,
    hash_password,
    normalize_email,
    session_cookie,
    token_hash,
    verify_password,
)
from noteworthy_app.core import extract_passages
from noteworthy_app.database import Database, PostgresAccountStore
from noteworthy_app.server import create_server
from noteworthy_app.storage import save_document
from tests.fixtures import study_pdf
from tests.postgres import PostgresTestCase, needs_postgres

CLASS_CODE = "cse4316-class-code"
PASSWORD = "correct horse battery"
JSON = {"Content-Type": "application/json"}
LOG_IN = {"error": "Log in to continue."}
WEEK = 7 * 24 * 60 * 60


class PasswordTests(unittest.TestCase):
    def test_hash_stores_its_parameters_and_a_random_salt(self):
        stored = hash_password(PASSWORD)
        scheme, n, r, p, salt, key = stored.split("$")
        self.assertEqual((scheme, n, r, p), ("scrypt", "16384", "8", "5"))
        self.assertEqual((len(bytes.fromhex(salt)), len(bytes.fromhex(key))), (16, 32))
        self.assertNotEqual(hash_password(PASSWORD), stored)
        self.assertTrue(verify_password(PASSWORD, stored))
        for wrong in ["correct horse batterY", "correct horse battery ", ""]:
            with self.subTest(wrong=wrong):
                self.assertFalse(verify_password(wrong, stored))

    def test_hashes_keep_working_when_the_parameters_change(self):
        # Hashes made before p went from 1 to 5 still match.
        with (
            mock.patch("noteworthy_app.accounts.SCRYPT_N", 2**10),
            mock.patch("noteworthy_app.accounts.SCRYPT_P", 1),
        ):
            older = hash_password(PASSWORD)
        self.assertTrue(older.startswith("scrypt$1024$8$1$"))
        self.assertTrue(verify_password(PASSWORD, older))
        self.assertFalse(verify_password("wrong password", older))

    def test_any_text_can_be_a_password(self):
        for password in ["contraseña segura", "\U0001f512 locked notes", "\ud800 lone half"]:
            with self.subTest(password=ascii(password)):
                self.assertTrue(verify_password(password, hash_password(password)))

    def test_malformed_stored_hash_never_matches(self):
        _, _, _, _, salt, key = hash_password(PASSWORD).split("$")
        for stored in [
            None,
            7,
            "",
            "scrypt",
            f"bcrypt$16384$8$1${salt}${key}",
            f"scrypt$16384$8$1${salt}${key}$",
            f"scrypt$x$8$1${salt}${key}",
            f"scrypt$3$8$1${salt}${key}",
            f"scrypt$-2$8$1${salt}${key}",
            f"scrypt${2**40}$8$1${salt}${key}",
            f"scrypt${2**70}$8$1${salt}${key}",
            f"scrypt$16384$8$1$zz${key}",
            f"scrypt$16384$8$1${salt}$",
        ]:
            with self.subTest(stored=stored):
                self.assertFalse(verify_password(PASSWORD, stored))

    def test_at_most_two_hashes_run_at_once(self):
        real = hashlib.scrypt
        lock = threading.Lock()
        running = [0]
        peak = [0]

        def scrypt(*args, **kwargs):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            try:
                # Long enough that the threads below overlap if nothing stops them.
                time.sleep(0.05)
                return real(*args, **kwargs)
            finally:
                with lock:
                    running[0] -= 1

        self.enterContext(mock.patch("noteworthy_app.accounts.SCRYPT_N", 2**10))
        stored = hash_password(PASSWORD)
        barrier = threading.Barrier(6)
        results = []

        def check(index):
            barrier.wait()
            if index % 2:
                results.append(verify_password(PASSWORD, stored))
            else:
                results.append(verify_password(PASSWORD, hash_password(PASSWORD)))

        with mock.patch("noteworthy_app.accounts.hashlib.scrypt", scrypt):
            threads = [threading.Thread(target=check, args=(index,)) for index in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertEqual(results, [True] * 6)
        self.assertEqual(peak[0], 2)


class HelperTests(unittest.TestCase):
    def test_email_is_trimmed_and_lowercased(self):
        self.assertEqual(normalize_email("  Student@UTA.Edu \r\n"), "student@uta.edu")
        for value in [
            "student",
            "student@uta",
            "@uta.edu",
            "stu dent@uta.edu",
            "a@b@uta.edu",
            "a" * 250 + "@x.edu",
            None,
            5,
            ["a@uta.edu"],
        ]:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "Enter a valid email address."),
            ):
                normalize_email(value)

    def test_session_cookie_flags(self):
        self.assertEqual(
            session_cookie("abc", 60, False),
            "noteworthy_session=abc; Max-Age=60; Path=/; HttpOnly; SameSite=Lax",
        )
        # Over HTTPS the name has the __Host- prefix, so another *.sslip.io site cannot set it.
        self.assertEqual(
            session_cookie(None, 60, True),
            "__Host-noteworthy_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax; Secure",
        )
        self.assertEqual(
            [Accounts(mock.Mock(), CLASS_CODE, 60, secure).cookie_name for secure in [False, True]],
            ["noteworthy_session", "__Host-noteworthy_session"],
        )

    def test_only_a_hash_of_the_token_is_kept(self):
        self.assertEqual(token_hash("abc"), hashlib.sha256(b"abc").hexdigest())

    def test_accounts_are_on_only_with_a_class_code(self):
        unused = Path("unused")
        for value in [None, "", "  ", "\r"]:
            with self.subTest(value=value), mock.patch.dict(os.environ):
                os.environ.pop("CLASS_CODE", None)
                if value is not None:
                    os.environ["CLASS_CODE"] = value
                # Other account settings are not read while accounts are off.
                os.environ["SESSION_DAYS"] = "never"
                self.assertIsNone(accounts_from_env(unused))
        settings = {"CLASS_CODE": f" {CLASS_CODE}\r", "SESSION_DAYS": "", "COOKIE_SECURE": ""}
        with mock.patch.dict(os.environ, settings):
            accounts = accounts_from_env(unused)
        self.assertEqual(
            (accounts.class_code, accounts.session_seconds, accounts.cookie_secure),
            (CLASS_CODE, WEEK, False),
        )
        settings = {"CLASS_CODE": "x" * 16, "SESSION_DAYS": " 2 ", "COOKIE_SECURE": "1\r"}
        with mock.patch.dict(os.environ, settings):
            accounts = accounts_from_env(unused)
        self.assertEqual((accounts.session_seconds, accounts.cookie_secure), (2 * 86400, True))
        for days in ["0", "-1", "1.5", "+2", "week", "²"]:
            with (
                self.subTest(days=days),
                mock.patch.dict(os.environ, {"CLASS_CODE": CLASS_CODE, "SESSION_DAYS": days}),
                self.assertRaisesRegex(ValueError, "SESSION_DAYS must be a whole number"),
            ):
                accounts_from_env(unused)
        # A short code could be guessed, so it stops startup.
        for code in ["cse4316", "x" * 15, f"  {'x' * 15}  "]:
            with (
                self.subTest(code=code),
                mock.patch.dict(os.environ, {"CLASS_CODE": code}),
                self.assertRaisesRegex(ValueError, "CLASS_CODE must be at least 16 characters"),
            ):
                accounts_from_env(unused)
        self.assertFalse(unused.exists())


class AccountStoreContract:
    """Checks every account store must pass; each subclass says how to make one."""

    def make_store(self):
        """Return a new store on the same saved data."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        # Faster hashes; PasswordTests use the real setting.
        self.enterContext(mock.patch("noteworthy_app.accounts.SCRYPT_N", 2**10))
        self.store = self.make_store()

    @staticmethod
    def account(**changes):
        return {
            "id": "a" * 32,
            "email": "student@uta.edu",
            "password_hash": "scrypt$16384$8$1$00$00",
            "created_at": "2026-11-16T00:00:00+00:00",
            **changes,
        }

    def test_accounts_persist_and_each_email_is_used_once(self):
        self.store.add_account(self.account())
        found = self.make_store().find_account("student@uta.edu")
        self.assertEqual(found, self.account())
        self.assertIsNone(self.store.find_account("other@uta.edu"))
        with self.assertRaises(AccountError) as caught:
            self.make_store().add_account(self.account(id="b" * 32))
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.find_account("student@uta.edu")["id"], "a" * 32)
        self.store.add_account(self.account(id="b" * 32, email="other@uta.edu"))
        self.assertEqual(self.store.find_account("other@uta.edu")["id"], "b" * 32)

    def test_concurrent_adds_of_one_email_keep_one_account(self):
        barrier = threading.Barrier(6)
        results = []

        def add(index):
            barrier.wait(timeout=30)
            try:
                self.store.add_account(self.account(id=f"{index:x}" * 32))
                results.append(index)
            except AccountError as exc:
                results.append(exc.status)

        threads = [threading.Thread(target=add, args=(index,)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        winner, *refused = sorted(results)
        self.assertEqual(refused, [409] * 5)
        self.assertEqual(self.store.find_account("student@uta.edu")["id"], f"{winner:x}" * 32)

    def test_sessions_are_added_found_and_deleted(self):
        self.store.add_account(self.account())
        key = token_hash("token")
        session = {"account_id": "a" * 32, "email": "student@uta.edu", "expires_at": 1_900_000_000}
        self.store.add_session(key, session)
        self.assertEqual(self.make_store().find_session(key), session)
        self.assertIsNone(self.store.find_session(token_hash("other")))
        self.store.delete_session(key)
        self.assertIsNone(self.store.find_session(key))
        self.store.delete_session(key)
        for key in ["../" + "a" * 61, "A" * 64, "a" * 63, None]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.store.find_session(key)
                with self.assertRaises(ValueError):
                    self.store.add_session(key, session)
                with self.assertRaises(ValueError):
                    self.store.delete_session(key)

    def test_sign_up_log_in_and_log_out(self):
        accounts = Accounts(self.store, CLASS_CODE, 3600, False)
        user, token = accounts.sign_up(" Student@UTA.edu ", PASSWORD, CLASS_CODE)
        self.assertEqual(user, {"email": "student@uta.edu"})
        account = self.store.find_account("student@uta.edu")
        self.assertTrue(verify_password(PASSWORD, account["password_hash"]))
        self.assertEqual(accounts.current(token), {"id": account["id"], "email": "student@uta.edu"})
        with self.assertRaises(AccountError) as caught:
            accounts.sign_up("STUDENT@uta.edu", PASSWORD, CLASS_CODE)
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(AccountError) as caught:
            accounts.log_in("student@uta.edu", "wrong password")
        self.assertEqual(caught.exception.status, 401)
        _, other = accounts.log_in("student@uta.edu", PASSWORD)
        accounts.log_out(token)
        self.assertIsNone(accounts.current(token))
        self.assertIsNone(self.store.find_session(token_hash(token)))
        accounts.log_out(token)
        # Each login has its own session.
        self.assertEqual(accounts.current(other)["id"], account["id"])

    def test_expired_session_is_removed(self):
        accounts = Accounts(self.store, CLASS_CODE, 3600, False)
        with mock.patch("noteworthy_app.accounts.time") as clock:
            clock.time.return_value = 1_800_000_000
            _, token = accounts.sign_up("student@uta.edu", PASSWORD, CLASS_CODE)
            key = token_hash(token)
            self.assertEqual(self.store.find_session(key)["expires_at"], 1_800_003_600)
            # Using the session does not move its expiry.
            clock.time.return_value = 1_800_003_599
            self.assertIsNotNone(accounts.current(token))
            clock.time.return_value = 1_800_003_600
            self.assertIsNone(accounts.current(token))
        self.assertIsNone(self.store.find_session(key))


class FileAccountStoreTests(AccountStoreContract, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "accounts"
        super().setUp()

    def make_store(self):
        return FileAccountStore(self.root)

    def test_email_is_hashed_into_the_file_name(self):
        self.store.add_account(self.account())
        # The email is hashed into the file name, so user input never becomes a path.
        name = hashlib.sha256(b"student@uta.edu").hexdigest()
        self.assertEqual([path.name for path in (self.root / "users").iterdir()], [f"{name}.json"])

    def test_unreadable_account_is_a_server_error(self):
        self.store.add_account(self.account())
        path = next((self.root / "users").iterdir())
        for content in [
            "{",
            "[]",
            "[" * 100000,
            json.dumps(self.account(id="../" + "a" * 29)),
            json.dumps(self.account(email="other@uta.edu")),
            json.dumps({"id": "a" * 32, "email": "student@uta.edu"}),
        ]:
            path.write_text(content)
            with (
                self.subTest(content=content[:40]),
                self.assertRaisesRegex(RuntimeError, "Saved account could not be read."),
            ):
                self.store.find_account("student@uta.edu")

    def test_unreadable_session_is_ignored(self):
        key = token_hash("token")
        session = {"account_id": "a" * 32, "email": "student@uta.edu", "expires_at": 1_900_000_000}
        self.store.add_session(key, session)
        path = self.root / "sessions" / f"{key}.json"
        for content in [
            "{",
            "[]",
            json.dumps({**session, "expires_at": "later"}),
            json.dumps({**session, "expires_at": True}),
            json.dumps({**session, "account_id": "../x"}),
        ]:
            path.write_text(content)
            with (
                self.subTest(content=content[:40]),
                self.assertLogs("noteworthy_app.accounts", level="WARNING"),
            ):
                self.assertIsNone(self.store.find_session(key))


@needs_postgres
class PostgresAccountStoreTests(AccountStoreContract, PostgresTestCase):
    def make_store(self):
        return PostgresAccountStore(self.database)

    def test_sign_ups_from_separate_app_processes_keep_one_account(self):
        # Each store has its own Database, as separate app processes would. The unique
        # email column keeps one account.
        stores = [PostgresAccountStore(Database(self.database_url)) for _ in range(3)]
        barrier = threading.Barrier(6)

        def add(index):
            barrier.wait(timeout=30)
            try:
                stores[index % len(stores)].add_account(self.account(id=f"{index:x}" * 32))
            except AccountError as exc:
                return exc.status
            return 201

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = sorted(pool.map(add, range(6)))
        self.assertEqual(results, [201] + [409] * 5)
        self.assertEqual(self.execute("SELECT count(*) FROM accounts"), [(1,)])

    def test_new_session_removes_expired_ones(self):
        self.store.add_account(self.account())
        now = int(time.time())
        expired = {"account_id": "a" * 32, "email": "student@uta.edu", "expires_at": now - 1}
        self.store.add_session("1" * 64, expired)
        self.store.add_session("2" * 64, {**expired, "expires_at": now + 3600})
        self.assertIsNone(self.store.find_session("1" * 64))
        self.assertEqual(self.store.find_session("2" * 64)["expires_at"], now + 3600)
        # Deleting an account deletes its sessions.
        self.execute("DELETE FROM accounts")
        self.assertIsNone(self.store.find_session("2" * 64))

    def test_email_postgres_cannot_hold_is_refused(self):
        for email in ["a\x00b@uta.edu", "\ud800@uta.edu"]:
            with self.subTest(email=ascii(email)):
                with self.assertRaisesRegex(ValueError, "valid email"):
                    self.store.add_account(self.account(email=email))
                self.assertIsNone(self.store.find_account(email))
        self.assertEqual(self.execute("SELECT count(*) FROM accounts"), [(0,)])


class AccountsTests(unittest.TestCase):
    def setUp(self):
        # Faster hashes; PasswordTests use the real setting.
        self.enterContext(mock.patch("noteworthy_app.accounts.SCRYPT_N", 2**10))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "accounts"
        self.accounts = Accounts(FileAccountStore(self.root), CLASS_CODE, 3600, False)

    def test_sign_up_saves_hashes_only_and_logs_in(self):
        user, token = self.accounts.sign_up(" Student@UTA.edu ", PASSWORD, f" {CLASS_CODE} ")
        self.assertEqual(user, {"email": "student@uta.edu"})
        self.assertEqual(self.accounts.current(token)["email"], "student@uta.edu")
        files = sorted(path for path in self.root.rglob("*") if path.is_file())
        self.assertEqual([path.parent.name for path in files], ["sessions", "users"])
        for path in files:
            content = path.read_bytes()
            self.assertNotIn(PASSWORD.encode(), content)
            self.assertNotIn(token.encode(), content)
        session, account = (json.loads(path.read_text()) for path in files)
        self.assertEqual(files[0].stem, token_hash(token))
        self.assertRegex(account["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(account["email"], "student@uta.edu")
        self.assertTrue(verify_password(PASSWORD, account["password_hash"]))
        self.assertEqual(
            (session["account_id"], session["email"]), (account["id"], "student@uta.edu")
        )
        self.assertEqual(
            self.accounts.current(token), {"id": account["id"], "email": "student@uta.edu"}
        )

    def test_wrong_class_code_is_refused_before_anything_else(self):
        for email, password, code in [
            ("student@uta.edu", PASSWORD, "wrong"),
            ("student@uta.edu", PASSWORD, ""),
            ("student@uta.edu", PASSWORD, CLASS_CODE.upper()),
            ("student@uta.edu", PASSWORD, CLASS_CODE + "x"),
            # Text that is not ASCII is compared as bytes, not refused with a TypeError.
            ("student@uta.edu", PASSWORD, "código"),
            ("student@uta.edu", PASSWORD, "\ud800"),
            # A bad email or password still gets the class code answer first.
            ("not an email", "short", "wrong"),
        ]:
            with self.subTest(code=ascii(code)):
                with self.assertRaises(AccountError) as caught:
                    self.accounts.sign_up(email, password, code)
                self.assertEqual(
                    (caught.exception.status, str(caught.exception)),
                    (403, "The class code is not correct."),
                )
        self.assertFalse(self.root.exists())

    def test_class_code_that_is_not_ascii(self):
        accounts = Accounts(FileAccountStore(self.root), "clase-ñ", 3600, False)
        user, _ = accounts.sign_up("student@uta.edu", PASSWORD, "clase-ñ")
        self.assertEqual(user, {"email": "student@uta.edu"})

    def test_sign_up_checks_each_field(self):
        for email, password, code, message in [
            (None, PASSWORD, CLASS_CODE, "must be strings"),
            ("student@uta.edu", None, CLASS_CODE, "must be strings"),
            ("student@uta.edu", PASSWORD, None, "must be strings"),
            ("student", PASSWORD, CLASS_CODE, "valid email"),
            ("student@uta.edu", "7 chars", CLASS_CODE, "at least 8 characters"),
            ("student@uta.edu", "x" * 1025, CLASS_CODE, "at most 1024 characters"),
        ]:
            with self.subTest(email=email, password=password, code=code):
                with self.assertRaisesRegex(ValueError, message):
                    self.accounts.sign_up(email, password, code)
        self.assertFalse(self.root.exists())
        # The password is not trimmed: spaces count and must match.
        self.accounts.sign_up("student@uta.edu", "  word  ", CLASS_CODE)
        with self.assertRaises(AccountError):
            self.accounts.log_in("student@uta.edu", "word")
        self.assertEqual(
            self.accounts.log_in("student@uta.edu", "  word  ")[0], {"email": "student@uta.edu"}
        )

    def test_concurrent_sign_ups_create_one_account(self):
        barrier = threading.Barrier(6)
        results = []

        def sign_up():
            barrier.wait()
            try:
                self.accounts.sign_up("student@uta.edu", PASSWORD, CLASS_CODE)
                results.append(201)
            except AccountError as exc:
                results.append(exc.status)

        threads = [threading.Thread(target=sign_up) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sorted(results), [201, 409, 409, 409, 409, 409])
        self.assertEqual(len(list((self.root / "users").iterdir())), 1)

    def test_login_failures_look_the_same(self):
        self.accounts.sign_up("student@uta.edu", PASSWORD, CLASS_CODE)
        failures = set()
        with mock.patch("noteworthy_app.accounts.verify_password", wraps=verify_password) as verify:
            for email, password in [
                ("other@uta.edu", PASSWORD),
                ("student@uta.edu", "wrong password"),
                ("", ""),
                ("x" * 5000, PASSWORD),
            ]:
                with self.assertRaises(AccountError) as caught:
                    self.accounts.log_in(email, password)
                failures.add((caught.exception.status, str(caught.exception)))
        self.assertEqual(failures, {(401, "Email or password is not correct.")})
        # An unknown email is still checked against a hash, so it takes as long.
        self.assertEqual(verify.call_count, 4)
        self.assertEqual(verify.call_args_list[0].args[1], self.accounts.dummy_hash)
        for email, password in [(None, PASSWORD), ("student@uta.edu", 5)]:
            with self.subTest(email=email), self.assertRaisesRegex(ValueError, "strings"):
                self.accounts.log_in(email, password)
        user, token = self.accounts.log_in("  STUDENT@uta.edu ", PASSWORD)
        self.assertEqual(user, {"email": "student@uta.edu"})
        self.assertEqual(self.accounts.current(token)["email"], "student@uta.edu")

    def assert_status(self, status, email, password):
        with self.assertRaises(AccountError) as caught:
            self.accounts.log_in(email, password)
        self.assertEqual(caught.exception.status, status)
        return str(caught.exception)

    def test_repeated_login_failures_wait_for_the_window(self):
        self.accounts.sign_up("student@uta.edu", PASSWORD, CLASS_CODE)
        with (
            mock.patch("noteworthy_app.accounts.time") as clock,
            mock.patch("noteworthy_app.accounts.verify_password", wraps=verify_password) as verify,
        ):
            clock.time.return_value = 1_800_000_000
            # A successful login clears the count.
            for _ in range(4):
                self.assert_status(401, "student@uta.edu", "wrong password")
            self.accounts.log_in("student@uta.edu", PASSWORD)
            for email in ["student@uta.edu", "nobody@uta.edu"]:
                for _ in range(5):
                    self.assert_status(401, email, "wrong password")
            verify.reset_mock()
            # Then even the right password is refused, without hashing. Unknown emails are
            # limited the same way, so a 429 does not show which emails have accounts.
            for email, password in [
                ("student@uta.edu", PASSWORD),
                (" STUDENT@uta.edu ", PASSWORD),
                ("nobody@uta.edu", PASSWORD),
            ]:
                with self.subTest(email=email):
                    self.assertEqual(
                        self.assert_status(429, email, password),
                        "Too many attempts. Try again later.",
                    )
            verify.assert_not_called()
            # Other emails are not affected.
            self.assert_status(401, "other@uta.edu", "wrong password")
            clock.time.return_value = 1_800_000_000 + 15 * 60
            self.assertEqual(
                self.accounts.log_in("student@uta.edu", PASSWORD)[0], {"email": "student@uta.edu"}
            )
            # Ended windows are dropped, so the table holds only recent emails.
            self.assertEqual(len(self.accounts.tries), 0)

    def test_database_errors_do_not_count_toward_the_login_limit(self):
        self.accounts.sign_up("student@uta.edu", PASSWORD, CLASS_CODE)
        down = RuntimeError("The database is not available. Try again later.")
        with mock.patch.object(self.accounts.store, "find_account", side_effect=down):
            for _ in range(6):
                with self.assertRaisesRegex(RuntimeError, "^The database is not available"):
                    self.accounts.log_in("student@uta.edu", PASSWORD)
        self.assertEqual(self.accounts.tries, {})
        # Once the database is back, the right password works.
        self.assertEqual(
            self.accounts.log_in("student@uta.edu", PASSWORD)[0], {"email": "student@uta.edu"}
        )
        # Wrong passwords before an error still count.
        for _ in range(4):
            self.assert_status(401, "student@uta.edu", "wrong password")
        with mock.patch.object(self.accounts.store, "find_account", side_effect=down):
            with self.assertRaises(RuntimeError):
                self.accounts.log_in("student@uta.edu", PASSWORD)
        self.assert_status(401, "student@uta.edu", "wrong password")
        self.assert_status(429, "student@uta.edu", PASSWORD)

    def test_parallel_logins_cannot_pass_the_limit(self):
        barrier = threading.Barrier(8)
        results = []

        def log_in():
            barrier.wait()
            try:
                self.accounts.log_in("nobody@uta.edu", PASSWORD)
            except AccountError as exc:
                results.append(exc.status)

        threads = [threading.Thread(target=log_in) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sorted(results), [401] * 5 + [429] * 3)

    def test_log_out_deletes_the_session(self):
        _, token = self.accounts.sign_up("student@uta.edu", PASSWORD, CLASS_CODE)
        _, other = self.accounts.log_in("student@uta.edu", PASSWORD)
        self.accounts.log_out(token)
        self.assertIsNone(self.accounts.current(token))
        self.accounts.log_out(token)
        # Each login has its own session.
        self.assertIsNotNone(self.accounts.current(other))
        self.assertEqual(
            [path.stem for path in (self.root / "sessions").iterdir()], [token_hash(other)]
        )

    def test_expired_session_is_removed(self):
        with mock.patch("noteworthy_app.accounts.time") as clock:
            clock.time.return_value = 1_800_000_000
            _, token = self.accounts.sign_up("student@uta.edu", PASSWORD, CLASS_CODE)
            session = self.root / "sessions" / f"{token_hash(token)}.json"
            self.assertEqual(json.loads(session.read_text())["expires_at"], 1_800_003_600)
            # Using the session does not move its expiry.
            clock.time.return_value = 1_800_003_599
            self.assertIsNotNone(self.accounts.current(token))
            clock.time.return_value = 1_800_003_600
            self.assertIsNone(self.accounts.current(token))
        self.assertFalse(session.exists())

    def test_malformed_tokens_are_not_looked_up(self):
        store = mock.Mock(wraps=FileAccountStore(self.root))
        accounts = Accounts(store, CLASS_CODE, 3600, False)
        for token in [None, 5, "", "abc", "a" * 42, "a" * 44, "a" * 42 + "!", "a" * 42 + "\n"]:
            with self.subTest(token=token):
                self.assertIsNone(accounts.current(token))
                accounts.log_out(token)
        store.find_session.assert_not_called()
        store.delete_session.assert_not_called()
        self.assertIsNone(accounts.current("a" * 43))
        store.find_session.assert_called_once_with(token_hash("a" * 43))


class AccountServerTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            mock.patch.dict(os.environ, {"RETRIEVAL": "keyword", "CLASS_CODE": CLASS_CODE})
        )
        os.environ.pop("COOKIE_SECURE", None)
        os.environ.pop("SESSION_DAYS", None)
        # These tests read the saved files, so never use a database from the shell.
        os.environ.pop("DATABASE_URL", None)
        # Faster hashes over HTTP; PasswordTests use the real setting.
        self.enterContext(mock.patch("noteworthy_app.accounts.SCRYPT_N", 2**10))
        # The fake model's reply is not JSON, so quizzes use cloze questions.
        self.enterContext(mock.patch("noteworthy_app.question_generator.LOGGER"))
        self.prompts = []
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.data_dir = Path(self.storage.name) / "documents"
        self.start()

    def tearDown(self):
        self.stop()

    def answer(self, prompt):
        self.prompts.append(prompt)
        return "Photosynthesis uses sunlight [p1-1]."

    def start(self):
        self.server = create_server(
            "127.0.0.1", 0, answer_generator=self.answer, data_dir=self.data_dir
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def restart(self, **settings):
        """Start a new server on the same data; a setting of None removes it."""
        self.stop()
        with mock.patch.dict(os.environ):
            for name, value in settings.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            self.start()

    def request(self, path, body=None, token=None, headers=None, method=None):
        headers = dict(headers or {})
        if token is not None:
            name = getattr(self.server.accounts, "cookie_name", COOKIE_NAME)
            headers["Cookie"] = f"{name}={token}"
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        self.addCleanup(connection.close)
        connection.request(method or ("GET" if body is None else "POST"), path, body, headers)
        response = connection.getresponse()
        return response.status, response.headers, response.read()

    def get(self, path, token=None, headers=None):
        status, _, content = self.request(path, token=token, headers=headers)
        return status, json.loads(content)

    def post(self, path, payload, token=None):
        status, _, content = self.request(path, json.dumps(payload), token, JSON)
        return status, json.loads(content)

    def token(self, headers):
        name, value = headers["Set-Cookie"].split(";", 1)[0].split("=", 1)
        self.assertEqual(name, self.server.accounts.cookie_name)
        return value

    def sign_up(self, email="a@uta.edu", password=PASSWORD):
        payload = {"email": email, "password": password, "class_code": CLASS_CODE}
        status, headers, content = self.request("/api/signup", json.dumps(payload), headers=JSON)
        self.assertEqual((status, json.loads(content)), (201, {"email": email}))
        return self.token(headers)

    def account_id(self, email):
        name = hashlib.sha256(email.encode()).hexdigest()
        path = self.data_dir / "accounts" / "users" / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))["id"]

    def upload(self, token, name="biology.pdf"):
        payload = {"name": name, "data": base64.b64encode(study_pdf()).decode()}
        status, document = self.post("/api/upload", payload, token)
        self.assertEqual(status, 200)
        return document["id"]

    def quiz_and_submit(self, token, document_id):
        status, quiz = self.post("/api/quiz", {"document_id": document_id}, token)
        self.assertEqual(status, 200)
        payload = {"quiz_id": quiz["quiz_id"], "answers": [None] * len(quiz["questions"])}
        status, result = self.post("/api/quiz/submit", payload, token)
        self.assertEqual(status, 200)
        return quiz, result

    def test_accounts_are_off_without_a_class_code(self):
        for index, value in enumerate([None, "", "  "]):
            with self.subTest(value=value):
                self.restart(CLASS_CODE=value)
                self.assertIsNone(self.server.accounts)
                # No body: a missing route is answered without reading one.
                for path in ["/api/signup", "/api/login", "/api/logout"]:
                    status, _, content = self.request(path, headers=JSON, method="POST")
                    self.assertEqual((status, json.loads(content)), (404, {"error": "Not found"}))
                self.assertEqual(self.get("/api/me"), (404, {"error": "Not found"}))
                # The browser's learner ID works as before.
                learner_id = str(index) * 32
                document_id = self.upload(None)
                _, quiz = self.post("/api/quiz", {"document_id": document_id})
                status, result = self.post(
                    "/api/quiz/submit",
                    {"quiz_id": quiz["quiz_id"], "learner_id": learner_id, "answers": [None] * 5},
                )
                self.assertEqual((status, result["learner_id"]), (200, learner_id))
                status, listed = self.get("/api/attempts", headers={"X-Learner-Id": learner_id})
                self.assertEqual(
                    [item["quiz_id"] for item in listed["attempts"]], [quiz["quiz_id"]]
                )
                status, documents = self.get("/api/documents")
                self.assertEqual([item["id"] for item in documents["documents"]], [document_id])
        self.assertFalse((self.data_dir / "accounts").exists())

    def test_sign_up_sets_a_session_cookie(self):
        payload = {"email": "a@uta.edu", "password": PASSWORD, "class_code": CLASS_CODE}
        status, headers, content = self.request("/api/signup", json.dumps(payload), headers=JSON)
        self.assertEqual((status, json.loads(content)), (201, {"email": "a@uta.edu"}))
        token = self.token(headers)
        self.assertRegex(token, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(
            headers["Set-Cookie"].split("; ")[1:],
            ["Max-Age=604800", "Path=/", "HttpOnly", "SameSite=Lax"],
        )
        self.assertEqual(self.get("/api/me", token), (200, {"email": "a@uta.edu"}))
        self.restart(COOKIE_SECURE="1", SESSION_DAYS="2")
        payload["email"] = "b@uta.edu"
        status, headers, _ = self.request("/api/signup", json.dumps(payload), headers=JSON)
        self.assertEqual(status, 201)
        self.assertEqual(
            headers["Set-Cookie"].split("; ")[1:],
            ["Max-Age=172800", "Path=/", "HttpOnly", "SameSite=Lax", "Secure"],
        )
        # Over HTTPS the cookie name has the __Host- prefix.
        self.assertTrue(headers["Set-Cookie"].startswith(f"{SECURE_COOKIE_NAME}="))
        # Sessions are kept across a restart.
        self.assertEqual(self.get("/api/me", token), (200, {"email": "a@uta.edu"}))
        # With COOKIE_SECURE=1 only the __Host- cookie counts. Another site under the same
        # parent domain can set the plain name, but not this one.
        plain = {"Cookie": f"{COOKIE_NAME}={token}"}
        self.assertEqual(self.get("/api/me", headers=plain), (401, LOG_IN))
        for days in ["0", "-3", "1.5", "week"]:
            with (
                self.subTest(days=days),
                mock.patch.dict(os.environ, {"SESSION_DAYS": days}),
                self.assertRaisesRegex(ValueError, "SESSION_DAYS"),
            ):
                create_server("127.0.0.1", 0, data_dir=self.data_dir)

    def test_sign_up_errors(self):
        self.sign_up("student@x.edu")
        for payload, expected in [
            ({"email": "b@uta.edu", "password": PASSWORD, "class_code": "wrong"}, 403),
            ({"email": "b@uta.edu", "password": "short", "class_code": CLASS_CODE}, 400),
            ({"email": "not an email", "password": PASSWORD, "class_code": CLASS_CODE}, 400),
            ({"email": "b@uta.edu", "password": PASSWORD}, 400),
            ({"email": "b@uta.edu", "class_code": CLASS_CODE}, 400),
            ({"email": ["b@uta.edu"], "password": PASSWORD, "class_code": CLASS_CODE}, 400),
            ({"email": " Student@X.edu ", "password": PASSWORD, "class_code": CLASS_CODE}, 409),
        ]:
            with self.subTest(payload=payload):
                status, headers, content = self.request(
                    "/api/signup", json.dumps(payload), headers=JSON
                )
                self.assertEqual(status, expected)
                self.assertIn("error", json.loads(content))
                self.assertIsNone(headers["Set-Cookie"])
        for body in ["{", "[]", "null", "[" * 5000]:
            with self.subTest(body=body[:10]):
                status, _, content = self.request("/api/signup", body, headers=JSON)
                self.assertEqual(status, 400)
                self.assertNotIn("recursion", json.loads(content)["error"])
        # Too large: refused before reading, so no body is sent.
        status, _, content = self.request(
            "/api/signup", headers={**JSON, "Content-Length": str(16 * 1024 + 1)}, method="POST"
        )
        self.assertEqual((status, json.loads(content)), (413, {"error": "Request is too large."}))
        # A form posted from another site cannot carry a JSON content type.
        valid = json.dumps({"email": "b@uta.edu", "password": PASSWORD, "class_code": CLASS_CODE})
        for content_type in ["application/x-www-form-urlencoded", "text/plain", None]:
            with self.subTest(content_type=content_type):
                headers = {"Content-Type": content_type} if content_type else {}
                status, _, content = self.request("/api/signup", valid, headers=headers)
                self.assertEqual(status, 415)
                self.assertIn("Content-Type", json.loads(content)["error"])
        self.assertEqual(len(list((self.data_dir / "accounts" / "users").iterdir())), 1)

    def test_login(self):
        self.sign_up("a@uta.edu")
        failures = []
        for payload in [
            {"email": "nobody@uta.edu", "password": PASSWORD},
            {"email": "a@uta.edu", "password": "wrong password"},
        ]:
            status, headers, content = self.request("/api/login", json.dumps(payload), headers=JSON)
            self.assertIsNone(headers["Set-Cookie"])
            failures.append((status, content))
        # The same reply for an unknown email and a wrong password.
        self.assertEqual(failures[0], failures[1])
        self.assertEqual(
            (failures[0][0], json.loads(failures[0][1])),
            (401, {"error": "Email or password is not correct."}),
        )
        self.assertEqual(self.post("/api/login", {"email": 5, "password": PASSWORD})[0], 400)
        # After five tries at one email in 15 minutes, the next waits for the window.
        payload = {"email": "nobody@uta.edu", "password": PASSWORD}
        for _ in range(4):
            self.assertEqual(self.post("/api/login", payload)[0], 401)
        status, headers, content = self.request("/api/login", json.dumps(payload), headers=JSON)
        self.assertEqual(
            (status, json.loads(content)), (429, {"error": "Too many attempts. Try again later."})
        )
        self.assertIsNone(headers["Set-Cookie"])
        payload = {"email": " A@UTA.edu", "password": PASSWORD}
        status, headers, content = self.request("/api/login", json.dumps(payload), headers=JSON)
        self.assertEqual((status, json.loads(content)), (200, {"email": "a@uta.edu"}))
        self.assertEqual(
            headers["Set-Cookie"].split("; ")[1:],
            ["Max-Age=604800", "Path=/", "HttpOnly", "SameSite=Lax"],
        )
        self.assertEqual(self.get("/api/me", self.token(headers)), (200, {"email": "a@uta.edu"}))

    def test_logout(self):
        token = self.sign_up()
        sessions = self.data_dir / "accounts" / "sessions"
        self.assertEqual(len(list(sessions.iterdir())), 1)
        # Logging out twice is fine; it needs no body.
        for _ in range(2):
            status, headers, content = self.request(
                "/api/logout", token=token, headers=JSON, method="POST"
            )
            self.assertEqual((status, json.loads(content)), (200, {"status": "logged out"}))
            self.assertEqual(
                headers["Set-Cookie"],
                "noteworthy_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax",
            )
        self.assertEqual(list(sessions.iterdir()), [])
        self.assertEqual(self.get("/api/me", token), (401, LOG_IN))
        self.assertEqual(self.get("/api/documents", token), (401, LOG_IN))
        # Without a session, with a body, it still answers.
        status, _, _ = self.request("/api/logout", "{}", headers=JSON)
        self.assertEqual(status, 200)
        # Another site cannot log a student out with a plain form.
        token = self.sign_up("b@uta.edu")
        status, _, _ = self.request("/api/logout", "x=1", token, method="POST")
        self.assertEqual(status, 415)
        self.assertEqual(self.get("/api/me", token), (200, {"email": "b@uta.edu"}))

    def test_other_cookies_do_not_hide_the_session(self):
        token = self.sign_up()
        session = f"{COOKIE_NAME}={token}"
        for header in [
            f"theme=dark; {session}",
            f"{session}; note=two words",
            f'prefs={{"theme":"dark"}}; {session}',
            f"Path=/; {session}",
            f'x="unclosed; {session}',
            f"{SECURE_COOKIE_NAME}=x; {session}",
        ]:
            with self.subTest(header=header):
                self.assertEqual(
                    self.get("/api/me", headers={"Cookie": header}), (200, {"email": "a@uta.edu"})
                )
        # A second session cookie can only come from another site under the same parent
        # domain, such as another *.sslip.io host. Neither is used, so that site cannot
        # swap in its own account.
        other = f"{COOKIE_NAME}={self.sign_up('b@uta.edu')}"
        for header in [
            f"{session}; {other}",
            f"{other}; {session}",
            f"{COOKIE_NAME}=old; {session}",
            f"{session}; {session}",
        ]:
            with self.subTest(header=header):
                self.assertEqual(self.get("/api/me", headers={"Cookie": header}), (401, LOG_IN))

    def test_data_routes_need_a_session(self):
        token = self.sign_up()
        document_id = self.upload(token)
        gets = [
            "/api/me",
            "/api/documents",
            "/api/progress",
            f"/api/progress?document_id={document_id}",
            "/api/attempts",
            "/api/quiz/" + "f" * 32,
            "/api/attempts/" + "f" * 32,
            # API paths added later are closed too.
            "/api/unknown",
        ]
        posts = [
            ("/api/upload", {"name": "b.pdf", "data": base64.b64encode(study_pdf()).decode()}),
            ("/api/ask", {"document_id": document_id, "question": "Photosynthesis sunlight"}),
            ("/api/quiz", {"document_id": document_id}),
            ("/api/quiz/submit", {"quiz_id": "f" * 32, "learner_id": "a" * 32, "answers": []}),
        ]
        for credentials in [
            {},
            {"token": "a" * 43},
            {"token": token[:-1]},
            {"token": token + "x"},
            {"headers": {"X-Learner-Id": "a" * 32}},
            {"headers": {"Cookie": f"other={token}"}},
            {"headers": {"Cookie": f'{COOKIE_NAME}="{token}'}},
        ]:
            for path in gets:
                with self.subTest(path=path, credentials=credentials):
                    self.assertEqual(self.get(path, **credentials), (401, LOG_IN))
            for path, payload in posts:
                with self.subTest(path=path, credentials=credentials):
                    headers = {**JSON, **credentials.get("headers", {})}
                    status, _, content = self.request(
                        path, json.dumps(payload), credentials.get("token"), headers
                    )
                    self.assertEqual((status, json.loads(content)), (401, LOG_IN))
        self.assertEqual(self.prompts, [])
        self.assertEqual(len(self.server.documents), 1)
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.get("/health"), (200, {"status": "ok"}))
        # With a session, unknown paths are still not found, and data must be sent as JSON.
        self.assertEqual(self.get("/api/unknown", token), (404, {"error": "Not found"}))
        status, _, _ = self.request("/api/unknown", token=token, headers=JSON, method="POST")
        self.assertEqual(status, 404)
        payload = json.dumps({"document_id": document_id})
        status, _, _ = self.request("/api/quiz", payload, token, {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertEqual(self.prompts, [])

    def test_large_refused_upload_still_gets_its_reply(self):
        # An upload is megabytes of base64. If the server replied without reading it all,
        # closing the socket would reset the connection before the client read the reply.
        body = json.dumps({"name": "big.pdf", "data": "A" * (4 * 1024 * 1024)})
        token = self.sign_up()
        wrong_type = (415, {"error": "Send JSON with Content-Type: application/json."})
        for credentials, headers, expected in [
            (None, JSON, (401, LOG_IN)),
            ("a" * 43, JSON, (401, LOG_IN)),
            (None, {"Content-Type": "text/plain"}, wrong_type),
            (token, {"Content-Type": "text/plain"}, wrong_type),
        ]:
            with self.subTest(credentials=credentials, headers=headers):
                status, _, content = self.request("/api/upload", body, credentials, headers)
                self.assertEqual((status, json.loads(content)), expected)
        self.assertEqual(len(self.server.documents), 0)

    def test_database_error_on_a_large_upload_still_gets_its_reply(self):
        # The session lookup fails before the body is read, as when the database is down.
        body = json.dumps({"name": "big.pdf", "data": "A" * (8 * 1024 * 1024)})
        token = self.sign_up()
        down = RuntimeError("The database is not available. Try again later.")
        with mock.patch.object(self.server.accounts.store, "find_session", side_effect=down):
            for _ in range(3):
                status, _, content = self.request("/api/upload", body, token, JSON)
                self.assertEqual((status, json.loads(content)), (503, {"error": str(down)}))
        self.assertEqual(len(self.server.documents), 0)

    def test_students_only_see_their_own_data(self):
        alice, bob = self.sign_up("a@uta.edu"), self.sign_up("b@uta.edu")
        document_id = self.upload(alice)
        status, _ = self.post(
            "/api/ask", {"document_id": document_id, "question": "Photosynthesis sunlight"}, alice
        )
        self.assertEqual(status, 200)
        quiz, result = self.quiz_and_submit(alice, document_id)
        self.assertEqual(set(quiz), {"quiz_id", "document_id", "questions"})
        prompts = len(self.prompts)

        self.assertEqual(self.get("/api/documents", bob), (200, {"documents": []}))
        for path in ["/api/ask", "/api/quiz"]:
            with self.subTest(path=path):
                payload = {"document_id": document_id, "question": "Photosynthesis sunlight"}
                self.assertEqual(
                    self.post(path, payload, bob),
                    (404, {"error": "Upload the PDF again to continue."}),
                )
        # Another student's document never reaches the model.
        self.assertEqual(len(self.prompts), prompts)
        not_found = (404, {"error": "Quiz not found. Generate a new quiz."})
        self.assertEqual(self.get(f"/api/quiz/{quiz['quiz_id']}", bob), not_found)
        payload = {"quiz_id": quiz["quiz_id"], "answers": [None] * 5}
        self.assertEqual(self.post("/api/quiz/submit", payload, bob), not_found)
        self.assertEqual(self.get(f"/api/attempts/{quiz['quiz_id']}", bob)[0], 404)
        self.assertEqual(self.get("/api/attempts", bob), (200, {"attempts": []}))
        for path in ["/api/progress", f"/api/progress?document_id={document_id}"]:
            self.assertEqual(self.get(path, bob), (200, {"topic_scores": {}}))
        self.assertFalse((self.data_dir / "attempts" / self.account_id("b@uta.edu")).exists())

        status, documents = self.get("/api/documents", alice)
        self.assertEqual([item["id"] for item in documents["documents"]], [document_id])
        self.assertEqual(self.get(f"/api/quiz/{quiz['quiz_id']}", alice), (200, quiz))
        self.assertEqual(self.get(f"/api/attempts/{quiz['quiz_id']}", alice), (200, result))
        status, attempts = self.get("/api/attempts", alice)
        self.assertEqual(
            [(item["quiz_id"], item["document_name"]) for item in attempts["attempts"]],
            [(quiz["quiz_id"], "biology.pdf")],
        )
        self.assertEqual(
            self.get("/api/progress", alice),
            (200, {"topic_scores": {"Page 1": {"correct": 0, "total": 5}}}),
        )

    def test_same_pdf_from_two_students_is_kept_twice(self):
        alice, bob = self.sign_up("a@uta.edu"), self.sign_up("b@uta.edu")
        first = self.upload(alice, "alice.pdf")
        second = self.upload(bob, "bob.pdf")
        self.assertNotEqual(first, second)
        # Uploading again keeps the student's ID.
        self.assertEqual(self.upload(alice, "alice.pdf"), first)
        self.assertEqual(len(list(self.data_dir.glob("*.json"))), 2)
        for document_id, email, name in [
            (first, "a@uta.edu", "alice.pdf"),
            (second, "b@uta.edu", "bob.pdf"),
        ]:
            metadata = json.loads((self.data_dir / f"{document_id}.json").read_text())
            self.assertEqual((metadata["name"], metadata["owner"]), (name, self.account_id(email)))
            self.assertEqual((self.data_dir / f"{document_id}.pdf").read_bytes(), study_pdf())
        for token, document_id, name in [(alice, first, "alice.pdf"), (bob, second, "bob.pdf")]:
            status, documents = self.get("/api/documents", token)
            self.assertEqual(
                [(item["id"], item["name"]) for item in documents["documents"]],
                [(document_id, name)],
            )

    def test_documents_from_before_accounts_are_hidden(self):
        pdf = study_pdf()
        old = save_document(self.data_dir, "old.pdf", pdf, extract_passages(pdf))
        self.restart()
        token = self.sign_up()
        self.assertEqual(self.get("/api/documents", token), (200, {"documents": []}))
        for path in ["/api/ask", "/api/quiz"]:
            payload = {"document_id": old, "question": "Photosynthesis sunlight"}
            self.assertEqual(self.post(path, payload, token)[0], 404)
        self.assertEqual(self.prompts, [])
        document_id = self.upload(token)
        quiz, _ = self.quiz_and_submit(token, document_id)

        # With accounts off again, only the old document is shared; students' data stays hidden.
        self.restart(CLASS_CODE=None)
        status, documents = self.get("/api/documents")
        self.assertEqual([item["id"] for item in documents["documents"]], [old])
        self.assertEqual(self.get(f"/api/quiz/{quiz['quiz_id']}")[0], 404)
        payload = {"document_id": document_id, "question": "Photosynthesis sunlight"}
        self.assertEqual(self.post("/api/ask", payload)[0], 404)

        # An old document is given to a student by adding the owner to its metadata.
        path = self.data_dir / f"{old}.json"
        metadata = json.loads(path.read_text())
        path.write_text(json.dumps({**metadata, "owner": self.account_id("a@uta.edu")}))
        self.restart(CLASS_CODE=CLASS_CODE)
        status, documents = self.get("/api/documents", token)
        self.assertEqual({item["id"] for item in documents["documents"]}, {old, document_id})

    def test_submit_uses_the_account_not_the_learner_id(self):
        token = self.sign_up()
        account_id = self.account_id("a@uta.edu")
        document_id = self.upload(token)
        _, quiz = self.post("/api/quiz", {"document_id": document_id}, token)
        saved = json.loads((self.data_dir / "quizzes" / f"{quiz['quiz_id']}.json").read_text())
        self.assertEqual(saved["owner"], account_id)
        self.assertEqual(set(quiz), {"quiz_id", "document_id", "questions"})
        payload = {"quiz_id": quiz["quiz_id"], "learner_id": "c" * 32, "answers": [None] * 5}
        status, result = self.post("/api/quiz/submit", payload, token)
        self.assertEqual((status, result["learner_id"]), (200, account_id))
        attempt = self.data_dir / "attempts" / account_id / f"{quiz['quiz_id']}.json"
        self.assertTrue(attempt.is_file())
        self.assertFalse((self.data_dir / "attempts" / ("c" * 32)).exists())
        # The header is ignored as well.
        status, attempts = self.get("/api/attempts", token, {"X-Learner-Id": "c" * 32})
        self.assertEqual([item["quiz_id"] for item in attempts["attempts"]], [quiz["quiz_id"]])

    def test_expired_session_is_refused(self):
        with mock.patch("noteworthy_app.accounts.time") as clock:
            clock.time.return_value = 1_800_000_000
            token = self.sign_up()
            self.assertEqual(self.get("/api/documents", token), (200, {"documents": []}))
            clock.time.return_value = 1_800_000_000 + WEEK
            self.assertEqual(self.get("/api/documents", token), (401, LOG_IN))
            self.assertEqual(self.post("/api/quiz", {"document_id": "x"}, token), (401, LOG_IN))
        self.assertEqual(list((self.data_dir / "accounts" / "sessions").iterdir()), [])

    def test_accounts_and_data_survive_a_restart(self):
        token = self.sign_up()
        document_id = self.upload(token)
        quiz, result = self.quiz_and_submit(token, document_id)
        self.restart()
        payload = {"email": "a@uta.edu", "password": PASSWORD}
        status, headers, _ = self.request("/api/login", json.dumps(payload), headers=JSON)
        self.assertEqual(status, 200)
        for current in [token, self.token(headers)]:
            status, documents = self.get("/api/documents", current)
            self.assertEqual([item["id"] for item in documents["documents"]], [document_id])
            status, attempts = self.get("/api/attempts", current)
            self.assertEqual(
                [(item["quiz_id"], item["document_name"]) for item in attempts["attempts"]],
                [(quiz["quiz_id"], "biology.pdf")],
            )
            self.assertEqual(self.get(f"/api/quiz/{quiz['quiz_id']}", current), (200, quiz))
            self.assertEqual(self.get(f"/api/attempts/{quiz['quiz_id']}", current), (200, result))


if __name__ == "__main__":
    unittest.main()
