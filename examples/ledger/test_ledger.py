import sqlite3, unittest
from ledger import transfer
class LedgerTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.executescript('CREATE TABLE accounts(id TEXT PRIMARY KEY,balance INTEGER CHECK(balance>=0)); INSERT INTO accounts VALUES("a",100),("b",50);')
 def balances(self):return list(self.db.execute('SELECT balance FROM accounts ORDER BY id'))
 def tearDown(self):self.db.close()
 def test_transfer(self):
  transfer(self.db,'a','b',20);self.assertEqual(self.balances(),[(80,),(70,)])
 def test_invalid_destination_is_atomic(self):
  with self.assertRaises(ValueError):transfer(self.db,'a','missing',20)
  self.assertEqual(self.balances(),[(100,),(50,)])
 def test_negative_rejected(self):
  with self.assertRaises(ValueError):transfer(self.db,'a','b',-20)
  self.assertEqual(self.balances(),[(100,),(50,)])
 def test_invalid_amounts_rejected(self):
  for amount in (0, True, False, 1.5, '20', None):
   with self.subTest(amount=amount):
    with self.assertRaises(ValueError):transfer(self.db,'a','b',amount)
    self.assertEqual(self.balances(),[(100,),(50,)])
 def test_missing_source_rejected(self):
  with self.assertRaises(ValueError):transfer(self.db,'missing','b',20)
  self.assertEqual(self.balances(),[(100,),(50,)])
 def test_overdraft_rejected(self):
  with self.assertRaises(ValueError):transfer(self.db,'a','b',101)
  self.assertEqual(self.balances(),[(100,),(50,)])
 def test_credit_failure_rolls_back_debit(self):
  self.db.execute("CREATE TRIGGER reject_credit BEFORE UPDATE ON accounts WHEN NEW.id='b' BEGIN SELECT RAISE(ABORT, 'credit rejected'); END")
  with self.assertRaises(sqlite3.IntegrityError):transfer(self.db,'a','b',20)
  self.assertEqual(self.balances(),[(100,),(50,)])
  self.assertFalse(self.db.in_transaction)
 def test_database_rollback_preserves_original_error(self):
  self.db.execute("CREATE TRIGGER reject_credit BEFORE UPDATE ON accounts WHEN NEW.id='b' BEGIN SELECT RAISE(ROLLBACK, 'credit rejected'); END")
  with self.assertRaisesRegex(sqlite3.IntegrityError, 'credit rejected'):
   transfer(self.db,'a','b',20)
  self.assertEqual(self.balances(),[(100,),(50,)])
  self.assertFalse(self.db.in_transaction)
 def test_success_preserves_caller_transaction(self):
  self.db.execute("UPDATE accounts SET balance=110 WHERE id='a'")
  transfer(self.db,'a','b',20)
  self.assertEqual(self.balances(),[(90,),(70,)])
  self.assertTrue(self.db.in_transaction)
  self.db.rollback()
  self.assertEqual(self.balances(),[(100,),(50,)])
 def test_failure_preserves_caller_changes(self):
  self.db.execute("CREATE TRIGGER reject_credit BEFORE UPDATE ON accounts WHEN NEW.id='b' BEGIN SELECT RAISE(ABORT, 'credit rejected'); END")
  self.db.execute("UPDATE accounts SET balance=110 WHERE id='a'")
  with self.assertRaises(sqlite3.IntegrityError):transfer(self.db,'a','b',20)
  self.assertEqual(self.balances(),[(110,),(50,)])
  self.assertTrue(self.db.in_transaction)
  self.db.rollback()
  self.assertEqual(self.balances(),[(100,),(50,)])
 def test_self_transfer_preserves_balance(self):
  transfer(self.db,'a','a',20)
  self.assertEqual(self.balances(),[(100,),(50,)])
 def test_standalone_transfer_can_be_rolled_back(self):
  transfer(self.db,'a','b',20)
  self.assertTrue(self.db.in_transaction)
  self.db.rollback()
  self.assertEqual(self.balances(),[(100,),(50,)])
 def test_autocommit_transfer(self):
  self.db.isolation_level=None
  transfer(self.db,'a','b',20)
  self.assertFalse(self.db.in_transaction)
  self.assertEqual(self.balances(),[(80,),(70,)])
 def test_credit_overflow_rejected(self):
  self.db.execute("UPDATE accounts SET balance=? WHERE id='b'",(2**63-1,))
  self.db.commit()
  with self.assertRaises(ValueError):transfer(self.db,'a','b',1)
  self.assertEqual(self.balances(),[(100,),(2**63-1,)])
  self.assertEqual(list(self.db.execute('SELECT typeof(balance) FROM accounts')), [('integer',),('integer',)])
 def test_credit_at_integer_limit(self):
  self.db.execute("UPDATE accounts SET balance=? WHERE id='b'",(2**63-2,))
  transfer(self.db,'a','b',1)
  self.assertEqual(self.balances(),[(99,),(2**63-1,)])
 def test_self_transfer_at_integer_limit(self):
  self.db.execute("UPDATE accounts SET balance=? WHERE id='a'",(2**63-1,))
  transfer(self.db,'a','a',1)
  self.assertEqual(self.balances(),[(2**63-1,),(50,)])
if __name__=='__main__':unittest.main()
