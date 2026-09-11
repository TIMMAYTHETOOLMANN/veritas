#!/usr/bin/env python3
"""Add gas_already_in_delta to ExecutionRecord and persist it."""
import pathlib

content = pathlib.Path('core/accounting.py').read_text()

# Add field to ExecutionRecord dataclass
content = content.replace(
    '''    gas_native: float = 0.0
    gas_usd: float = 0.0
    created_at: int = 0
    verified_at: int = 0''',
    '''    gas_native: float = 0.0
    gas_usd: float = 0.0
    gas_already_in_delta: bool = True
    created_at: int = 0
    verified_at: int = 0'''
)

# Add to to_dict()
content = content.replace(
    '''            "gas_native": round(self.gas_native, 8),
            "gas_usd": round(self.gas_usd, 6),
            "created_at": self.created_at,''',
    '''            "gas_native": round(self.gas_native, 8),
            "gas_usd": round(self.gas_usd, 6),
            "gas_already_in_delta": self.gas_already_in_delta,
            "created_at": self.created_at,'''
)

# Update record_execution to store gas_already_in_delta
content = content.replace(
    '''                INSERT INTO executions (
                    tx_hash, chain_id, block_number, route_id, candidate_id,
                    status, projected_profit_usd, realized_profit_usd,
                    verified_profit_usd, gas_native, gas_usd, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
    '''                INSERT INTO executions (
                    tx_hash, chain_id, block_number, route_id, candidate_id,
                    status, projected_profit_usd, realized_profit_usd,
                    verified_profit_usd, gas_native, gas_usd, gas_already_in_delta,
                    created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
)

# Update the VALUES tuple in record_execution
content = content.replace(
    '''                record.projected_profit_usd, record.realized_profit_usd,
                record.verified_profit_usd, record.gas_native, record.gas_usd,
                record.created_at, record.verified_at,''',
    '''                record.projected_profit_usd, record.realized_profit_usd,
                record.verified_profit_usd, record.gas_native, record.gas_usd,
                int(record.gas_already_in_delta),
                record.created_at, record.verified_at,'''
)

# Update verify_execution to store gas_already_in_delta
content = content.replace(
    '''        # Apply accounting equation
        if gas_already_in_delta:
            record.verified_profit_usd = realized_profit_usd
        else:
            record.verified_profit_usd = realized_profit_usd - gas_usd''',
    '''        # Apply accounting equation
        record.gas_already_in_delta = gas_already_in_delta
        if gas_already_in_delta:
            record.verified_profit_usd = realized_profit_usd
        else:
            record.verified_profit_usd = realized_profit_usd - gas_usd'''
)

# Update _update_record to persist gas_already_in_delta
content = content.replace(
    '''                UPDATE executions SET
                    block_number = ?, status = ?,
                    realized_profit_usd = ?, verified_profit_usd = ?,
                    gas_native = ?, gas_usd = ?, verified_at = ?
                WHERE tx_hash = ?''',
    '''                UPDATE executions SET
                    block_number = ?, status = ?,
                    realized_profit_usd = ?, verified_profit_usd = ?,
                    gas_native = ?, gas_usd = ?, gas_already_in_delta = ?,
                    verified_at = ?
                WHERE tx_hash = ?'''
)

# Update the VALUES tuple in _update_record
content = content.replace(
    '''                record.realized_profit_usd, record.verified_profit_usd,
                record.gas_native, record.gas_usd, record.verified_at,
                record.tx_hash,''',
    '''                record.realized_profit_usd, record.verified_profit_usd,
                record.gas_native, record.gas_usd,
                int(record.gas_already_in_delta), record.verified_at,
                record.tx_hash,'''
)

pathlib.Path('core/accounting.py').write_text(content)
print("Added gas_already_in_delta to ExecutionRecord")
