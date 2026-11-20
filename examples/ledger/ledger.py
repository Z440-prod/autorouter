def transfer(db, source, destination, amount):
    """Move money atomically and reject an overdraft."""
    if type(amount) is not int or amount <= 0:
        raise ValueError('amount must be a positive integer')
    # Keep the caller's commit/rollback control on non-autocommit connections.
    started_transaction = not db.in_transaction and db.isolation_level is not None
    if started_transaction:
        db.execute('BEGIN ' + db.isolation_level)
    db.execute('SAVEPOINT ledger_transfer')
    try:
        source_row = db.execute('SELECT balance FROM accounts WHERE id=?', (source,)).fetchone()
        destination_row = db.execute('SELECT balance FROM accounts WHERE id=?', (destination,)).fetchone()
        if source_row is None or destination_row is None:
            raise ValueError('account does not exist')
        if source_row[0] < amount:
            raise ValueError('insufficient funds')
        if source != destination and destination_row[0] + amount > 2**63 - 1:
            raise ValueError('destination balance would overflow')
        db.execute('UPDATE accounts SET balance=balance-? WHERE id=?', (amount, source))
        db.execute('UPDATE accounts SET balance=balance+? WHERE id=?', (amount, destination))
        db.execute('RELEASE SAVEPOINT ledger_transfer')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK TO SAVEPOINT ledger_transfer')
            db.execute('RELEASE SAVEPOINT ledger_transfer')
            if started_transaction:
                db.rollback()
        raise
