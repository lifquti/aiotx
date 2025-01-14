import asyncio
import json
import sys
from decimal import Decimal
from typing import Optional, Union

import aiohttp
from aiohttp.client_exceptions import ClientError, ClientOSError

from aiotx.clients._base_client import AioTxClient, BlockMonitor
from aiotx.exceptions import (
    BlockNotFoundError,
    CreateTransactionError,
    InsufficientFunds,
    InternalJSONRPCError,
    InvalidArgumentError,
    InvalidRequestError,
    MethodNotFoundError,
    NotImplementedError,
    RpcConnectionError,
)
from aiotx.log import logger
from aiotx.types import FeeEstimate, UTXOType


class AioTxUTXOClient(AioTxClient):
    def __init__(
        self,
        node_url: str,
        headers,
        testnet: bool,
        node_username,
        node_password,
        network_name,
        db_url,
    ):
        try:
            from bitcoinlib.networks import Network

        except ImportError:
            logger.error(
                "The required dependencies for AioTxLTCClient or AioTxLTCClient are not installed. "
                "Please install the necessary packages and try again."
                "pip install aiotx[utxo]"
            )
            sys.exit(-1)

        super().__init__(node_url, headers)
        self.node_username = node_username
        self.node_password = node_password
        self.testnet = testnet
        self._network = Network(network_name)
        self.monitor = UTXOMonitor(self, db_url)
        asyncio.run(self.monitor._init_db())

    @staticmethod
    def to_satoshi(amount: Union[int, float, str]) -> int:
        return int(Decimal(str(amount)) * Decimal(10**8))

    @staticmethod
    def from_satoshi(amount: int) -> float:
        return amount / 10**8

    async def generate_address(self) -> tuple[str, str]:
        from bitcoinlib.encoding import pubkeyhash_to_addr_bech32
        from bitcoinlib.keys import HDKey

        hdkey = HDKey()
        derivation_path = f"m/84'/{self._network.bip44_cointype}'/0'/0/0"
        private_key = hdkey.subkey_for_path(derivation_path).private_hex
        hash160 = hdkey.subkey_for_path(derivation_path).hash160
        address = pubkeyhash_to_addr_bech32(
            hash160, prefix=self._network.prefix_bech32, witver=0, separator="1"
        )
        last_block_number = await self.get_last_block_number()
        await self.import_address(address, last_block_number)
        return private_key, address

    def get_address_from_private_key(self, private_key):
        from bitcoinlib.encoding import pubkeyhash_to_addr_bech32
        from bitcoinlib.keys import Key

        key = Key(private_key)
        address = pubkeyhash_to_addr_bech32(
            key.hash160, prefix=self._network.prefix_bech32, witver=0, separator="1"
        )
        return {
            "private_key": private_key,
            "public_key": key.public_hex,
            "address": address,
        }

    async def import_address(self, address: str, block_number: Optional[int] = None):
        await self.monitor._add_new_address(address, block_number)

    async def get_last_block_number(self) -> int:
        payload = {"method": "getblockcount", "params": []}
        result = await self._make_rpc_call(payload)
        return result["result"]

    async def get_block_by_number(self, block_number: int, verbosity: int = 2):
        payload = {"method": "getblockhash", "params": [block_number]}
        block_hash = await self._make_rpc_call(payload)
        payload = {"method": "getblock", "params": [block_hash["result"], verbosity]}
        result = await self._make_rpc_call(payload)
        return result["result"]

    async def get_balance(self, address: str) -> int:
        utxo_data: list[UTXOType] = await self.monitor._get_utxo_data(address)
        if len(utxo_data) == 0:
            return 0
        return sum(utxo.amount_satoshi for utxo in utxo_data)

    async def speed_up_transaction_by_self_child_payment(
        self,
        private_key: str,
        tx_id: str,
        conf_target: int = 6,
        estimate_mode: FeeEstimate = FeeEstimate.CONSERVATIVE,
        total_fee: Optional[int] = None,
        fee_per_byte: Optional[int] = None,
    ):
        from_wallet = self.get_address_from_private_key(private_key)
        from_address = from_wallet["address"]

        outputs_from_slow_transaction = await self.monitor._get_utxo(tx_id)
        address_utxo_list: list[UTXOType] = await self.monitor._get_utxo_data(
            from_address
        )
        # Adding UTXO from database in case we need more UTXO to cover transaction costs
        for utxo_to_use in outputs_from_slow_transaction:
            try:
                address_utxo_list.remove(utxo_to_use)
            except ValueError:
                raise CreateTransactionError(
                    "Unable to allocate unspent outputs from slow transaction"
                )
            address_utxo_list.insert(0, utxo_to_use)

        if total_fee is None:
            # putting here 0 to just count total transaction fee based on weight
            # We are multiplying result on 3 because we need to cover old transaction too
            destinations = {from_address: 0}
            total_fee = (
                await self._estimate_total_fee(
                    private_key,
                    destinations,
                    conf_target,
                    estimate_mode,
                    fee_per_byte,
                    address_utxo_list,
                    from_address,
                    True,
                )
                * 3
            )

        # putting total_fee * 2 to get real number of inputs to be used,
        # because we will not be able to send empty transaction into network
        # and we will also deduct commission from amount
        destinations = {from_address: total_fee}

        _, inputs_used, _ = await self._create_transaction(
            destinations, address_utxo_list, from_address, total_fee, True
        )
        # Getting real sum to be sended to our own wallet to spend all the UTXO's what we will use for that
        total_sum = sum([satoshi_amount for _, _, satoshi_amount in inputs_used])
        destinations = {from_address: total_sum}

        return await self._build_and_send_transaction(
            private_key,
            destinations,
            conf_target,
            estimate_mode,
            total_fee,
            fee_per_byte,
            True,
        )

    async def _estimate_total_fee(
        self,
        private_key: str,
        destinations: dict,
        conf_target: int,
        estimate_mode: FeeEstimate,
        fee_per_byte: Optional[int],
        utxo_list: list,
        from_address: str,
        deduct_fee: bool,
    ):
        empty_fee_transaction, _, _ = await self._create_transaction(
            destinations, utxo_list, from_address, 0, deduct_fee
        )
        if fee_per_byte is None:
            fee_per_kb = await self.estimate_smart_fee(conf_target, estimate_mode)
        else:
            fee_per_kb = fee_per_byte * 1024
        empty_fee_transaction.fee_per_kb = fee_per_kb
        empty_fee_transaction = self._sign_transaction(
            empty_fee_transaction, [private_key]
        )
        empty_fee_transaction.estimate_size()
        return empty_fee_transaction.calculate_fee()

    async def _build_and_send_transaction(
        self,
        private_key: str,
        destinations: dict[str, int],
        conf_target: int,
        estimate_mode: FeeEstimate,
        total_fee: Optional[int],
        fee_per_byte: Optional[int],
        deduct_fee: bool,
    ) -> str:
        from_wallet = self.get_address_from_private_key(private_key)
        from_address = from_wallet["address"]
        utxo_list: list[UTXOType] = await self.monitor._get_utxo_data(from_address)

        if total_fee is None:
            total_fee = await self._estimate_total_fee(
                private_key,
                destinations,
                conf_target,
                estimate_mode,
                fee_per_byte,
                utxo_list,
                from_address,
                deduct_fee,
            )

        transaction, inputs_used, outputs_used = await self._create_transaction(
            destinations, utxo_list, from_address, total_fee, deduct_fee
        )

        signed_tx = self._sign_transaction(
            transaction, [private_key] * len(inputs_used)
        )

        txid = await self._send_transaction(signed_tx.raw_hex())
        await self._mark_inputs_as_used(inputs_used)
        await self._save_pending_outputs(outputs_used, from_address, txid)
        return txid

    async def _save_pending_outputs(self, outputs_used, from_address: str, tx_id: str):
        for i, output in enumerate(outputs_used):
            destination_address = output[0]
            if from_address != destination_address:
                continue
            amount = output[1]
            await self.monitor._add_new_utxo(from_address, tx_id, amount, i)

    async def send(
        self,
        private_key: str,
        to_address: str,
        amount: int,
        total_fee: Optional[int] = None,
        fee_per_byte: Optional[int] = None,
        conf_target: int = 6,
        estimate_mode: FeeEstimate = FeeEstimate.CONSERVATIVE,
        deduct_fee: bool = False,
    ) -> str:
        return await self._build_and_send_transaction(
            private_key,
            {to_address: amount},
            conf_target,
            estimate_mode,
            total_fee,
            fee_per_byte,
            deduct_fee,
        )

    async def send_bulk(
        self,
        private_key: str,
        destinations: dict[str, int],
        total_fee: Optional[int] = None,
        fee_per_byte: int = None,
        conf_target: int = 6,
        estimate_mode: FeeEstimate = FeeEstimate.CONSERVATIVE,
        deduct_fee: bool = False,
    ) -> str:
        return await self._build_and_send_transaction(
            private_key,
            destinations,
            conf_target,
            estimate_mode,
            total_fee,
            fee_per_byte,
            deduct_fee,
        )

    async def get_raw_transaction(self, tx_id: str, verbosity: int = 2) -> dict:
        payload = {"method": "getrawtransaction", "params": [tx_id, verbosity]}
        transaction_data = await self._make_rpc_call(payload)
        return transaction_data["result"]

    async def get_tx_fee(self, tx_id: str) -> int:
        total_output = 0
        total_input = 0
        raw_tx_data = await self.get_raw_transaction(tx_id)
        for output in raw_tx_data["vout"]:
            total_output += self.to_satoshi(output["value"])
        tx_inputs = await self._get_tx_inputs_details(raw_tx_data)
        for _, _, input_value in tx_inputs:
            total_input += input_value
        return total_input - total_output

    async def _get_tx_inputs_details(self, raw_tx_data: dict) -> list[tuple]:
        inputs_list = []
        for input in raw_tx_data["vin"]:
            if input.get("txid") is None:
                raise NotImplementedError(
                    "Miner transactions processing are not implemented yet!"
                )
            input_tx_data = await self.get_raw_transaction(input["txid"])
            for output in input_tx_data["vout"]:
                if output["n"] != input["vout"]:
                    continue
                inputs_list.append(
                    (input["txid"], output["n"], self.to_satoshi(output["value"]))
                )
        return inputs_list

    async def _create_transaction(
        self,
        destinations: dict[str, int],
        utxo_list: list[UTXOType],
        from_address: str,
        fee: int,
        deduct_fee: bool,
    ):
        from bitcoinlib.transactions import Transaction

        transaction = Transaction(network=self._network.name, witness_type="segwit")
        inputs = []
        outputs = []
        total_amount = sum(destinations.values())
        total_value = 0
        for utxo in utxo_list:
            input_data = (utxo.tx_id, utxo.output_n, utxo.amount_satoshi)
            inputs.append(input_data)
            total_value += utxo.amount_satoshi
            if total_value >= total_amount + fee:
                break
        total_spend = total_amount + fee if not deduct_fee else total_amount
        if total_value < total_spend:
            raise InsufficientFunds(
                f"We have only {total_value} satoshi and it's {total_amount + fee} at least needed to cover that transaction!"
            )

        if deduct_fee:
            leftover = total_value - total_amount
        else:
            leftover = int(total_value - total_amount - fee)

        if leftover > 0:
            outputs.append((from_address, leftover))

        deducted_fee_amount = 0 if not deduct_fee else fee / len(destinations)

        for address, amount in destinations.items():
            outputs.append((address, int(amount - deducted_fee_amount)))

        for input_data in inputs:
            prev_tx_id, prev_out_index, value = input_data
            transaction.add_input(
                prev_txid=prev_tx_id,
                output_n=prev_out_index,
                value=value,
                witness_type="segwit",
            )

        for output_data in outputs:
            address, value = output_data
            transaction.add_output(value=value, address=address)

        return transaction, inputs, outputs

    def _sign_transaction(self, transaction, private_keys: list[str]):
        from bitcoinlib.keys import Key

        for i, private_key in enumerate(private_keys):
            key = Key(private_key)
            transaction.sign(key, i)
        return transaction

    async def _mark_inputs_as_used(self, inputs: list) -> None:
        for input in inputs:
            await self.monitor._mark_utxo_used(input[0], input[1])

    async def _send_transaction(self, raw_transaction: str) -> str:
        payload = {"method": "sendrawtransaction", "params": [raw_transaction]}
        result = await self._make_rpc_call(payload)
        return result["result"]

    async def estimate_smart_fee(
        self,
        conf_target: int = 6,
        estimate_mode: FeeEstimate = FeeEstimate.CONSERVATIVE,
    ) -> int:
        payload = {
            "method": "estimatesmartfee",
            "params": [conf_target, estimate_mode.value],
        }
        result = await self._make_rpc_call(payload)
        return self.to_satoshi(result["result"]["feerate"])

    def _check_connection(self) -> None:
        return None

    async def _make_rpc_call(self, payload) -> dict:
        payload["jsonrpc"] = "2.0"
        payload["id"] = "curltest"
        logger.info(f"rpc call payload: {payload}")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.node_url,
                data=json.dumps(payload),
                headers=self._headers,
                auth=aiohttp.BasicAuth(self.node_username, self.node_password),
            ) as response:
                if response.status != 200:
                    raise RpcConnectionError(await response.text())
                result = await response.json()
                error = result.get("error")
                logger.info(f"rpc call result: {result}")
                if error is None:
                    return result

                error_code = error.get("code")
                error_message = error.get("message")
                if error_code == -5:
                    raise BlockNotFoundError(error_message)
                elif error_code == -8:
                    raise InvalidArgumentError(error_message)
                elif error_code == -32600:
                    raise InvalidRequestError(error_message)
                elif error_code == -32601:
                    raise MethodNotFoundError(error_message)
                elif error_code == -32603:
                    raise InternalJSONRPCError(error_message)
                else:
                    raise RpcConnectionError(f"Error {error_code}: {error_message}")


class UTXOMonitor(BlockMonitor):
    def __init__(self, client: AioTxUTXOClient, db_url):
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool

        from aiotx.utils.utxo_db_models import (
            create_address_model,
            create_last_block_model,
            create_utxo_model,
        )

        self.client = client
        self.block_handlers = []
        self.transaction_handlers = []
        self.new_utxo_transaction_handlers = []
        self.block_transactions_handlers = []
        self.running = False
        self._db_url = db_url
        self._engine = create_async_engine(db_url, poolclass=NullPool)
        self._session = sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )
        self.Address = create_address_model(self.client._network.name)
        self.UTXO = create_utxo_model(self.client._network.name)
        self.LastBlock = create_last_block_model(self.client._network.name)

    async def poll_blocks(self, _: int):
        network_last_block = await self.client.get_last_block_number()
        local_latest_block = await self._get_last_block()
        if local_latest_block is None:
            local_latest_block = network_last_block
        if network_last_block < local_latest_block:
            return
        block_data = await self.client.get_block_by_number(local_latest_block)
        await self.process_block(local_latest_block, block_data)
        next_block = local_latest_block + 1
        await self._update_last_block(next_block)

    async def process_block(self, block_number, block_data):
        await self._update_last_block(block_number)
        for handler in self.block_handlers:
            await handler(block_number)

        addresses = await self._get_addresses()
        for transaction in block_data["tx"]:
            for output in transaction["vout"]:
                outputs_scriptPubKey = output.get("scriptPubKey")
                if outputs_scriptPubKey is None:
                    continue

                output_address_list = outputs_scriptPubKey.get("addresses")
                output_address = outputs_scriptPubKey.get("address")

                if output_address is None and output_address_list is None:
                    continue

                if output_address_list is not None:
                    to_address = output_address_list[0]
                else:
                    to_address = output_address

                if to_address not in addresses:
                    continue

                value = self.client.to_satoshi(output["value"])
                output_n = output["n"]
                await self._add_new_utxo(
                    to_address, transaction["txid"], value, output_n
                )

                for handler in self.new_utxo_transaction_handlers:
                    await handler(transaction)

        all_utxo_tx_ids = await self._get_all_utxo_tx_ids()
        for transaction in block_data["tx"]:
            for input_utxo in transaction["vin"]:
                txid = input_utxo.get("txid")
                vout = input_utxo.get("vout")
                if txid is None or vout is None:
                    continue
                if txid in all_utxo_tx_ids:
                    await self._process_input_utxo(txid, vout)

        for transaction in block_data["tx"]:
            for handler in self.transaction_handlers:
                await handler(transaction)
        for handler in self.block_transactions_handlers:
            await handler(block_data["tx"])

    async def _init_db(self) -> None:
        from aiotx.utils.utxo_db_models import Base

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            try:
                last_known_block = await self.client.get_last_block_number()
            except (ClientError, ClientOSError) as e:
                logger.warning(
                    f"Can't get the last known block number during database initialization for the UTXO client. Please check your connection. {e}"
                )
            else:
                await self._init_last_block(last_known_block)

    async def _drop_tables(self) -> None:
        from aiotx.utils.utxo_db_models import Base

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def _add_new_address(self, address: str, block_number: int = None) -> None:
        from sqlalchemy import select

        last_known_block = await self.client.get_last_block_number()
        if block_number is None:
            block_number = last_known_block

        async with self._session() as session:
            async with session.begin():
                existing_address = await session.scalar(
                    select(self.Address).filter_by(address=address)
                )
                if existing_address:
                    existing_address.block_number = block_number
                else:
                    session.add(
                        self.Address(address=address, block_number=block_number)
                    )
                await session.commit()
        last_known_block = await self._get_last_block()
        if last_known_block is None or last_known_block > block_number:
            await self._update_last_block(block_number)

    async def _add_new_utxo(
        self, address: str, tx_id: str, amount: int, output_n: int
    ) -> None:
        from sqlalchemy import select

        async with self._session() as session:
            async with session.begin():
                existing_utxo = await session.scalar(
                    select(self.UTXO).filter_by(
                        address=address, tx_id=tx_id, output_n=output_n
                    )
                )
                if existing_utxo:
                    existing_utxo.used = False
                else:
                    session.add(
                        self.UTXO(
                            address=address,
                            tx_id=tx_id,
                            amount_satoshi=amount,
                            output_n=output_n,
                        )
                    )
                await session.commit()

    async def _update_last_block(self, block_number: int) -> None:
        from sqlalchemy import select

        async with self._session() as session:
            async with session.begin():
                data = await session.execute(select(self.LastBlock))
                last_block = data.scalar()
                if last_block is None:
                    await self._init_last_block(block_number)
                else:
                    last_block.block_number = block_number
                    await session.commit()

    async def _init_last_block(self, block_number: int) -> None:
        from sqlalchemy import select

        async with self._session() as session:
            async with session.begin():
                data = await session.execute(select(self.LastBlock))
                last_block = data.scalar()
                if last_block is None:
                    session.add(self.LastBlock(block_number=block_number))
                await session.commit()

    async def _process_input_utxo(self, txid: str, vout: int) -> None:
        utxo_list: list[UTXOType] = await self._get_utxo(txid, vout)
        for utxo in utxo_list:
            await self._delete_utxo(utxo.tx_id, utxo.output_n)

    async def _get_utxo_data(self, address: str, spent=False) -> list[UTXOType]:
        from sqlalchemy import select

        async with self._session() as session:
            result = await session.execute(
                select(
                    self.UTXO.tx_id,
                    self.UTXO.output_n,
                    self.UTXO.amount_satoshi,
                    self.UTXO.used,
                ).where((self.UTXO.address == address) & (self.UTXO.used == spent))
            )
            rows = result.fetchall()
            return [
                UTXOType(
                    tx_id=row.tx_id,
                    output_n=row.output_n,
                    address=address,
                    amount_satoshi=row.amount_satoshi,
                    used=row.used,
                )
                for row in rows
            ]

    async def _mark_utxo_used(self, tx_id: str, output_n: str) -> None:
        from sqlalchemy import update

        async with self._session() as session:
            async with session.begin():
                stmt = (
                    update(self.UTXO)
                    .where(
                        (self.UTXO.tx_id == tx_id) & (self.UTXO.output_n == output_n)
                    )
                    .values(used=True)
                )
                await session.execute(stmt)
                await session.commit()

    async def _get_utxo(
        self, tx_id: str, output_n: Optional[int] = None
    ) -> list[UTXOType]:
        from sqlalchemy import select

        async with self._session() as session:
            query = select(self.UTXO).where(self.UTXO.tx_id == tx_id)
            if output_n is not None:
                query = query.where(self.UTXO.output_n == output_n)

            result = await session.execute(query)
            rows = result.scalars().all()
            return [
                UTXOType(
                    tx_id=row.tx_id,
                    output_n=row.output_n,
                    address=row.address,
                    amount_satoshi=row.amount_satoshi,
                    used=row.used,
                )
                for row in rows
            ]

    async def _get_all_utxo_tx_ids(self) -> list[str]:
        from sqlalchemy import select

        """
        Function to optimize UTXO checking on new block to not make a SELECT for each input in the block
        """
        async with self._session() as session:
            result = await session.execute(select(self.UTXO.tx_id))
            return {row[0] for row in result.fetchall()}

    async def _delete_utxo(self, tx_id: str, output_n: int) -> None:
        async with self._session() as session:
            async with session.begin():
                await session.delete(await session.get(self.UTXO, (tx_id, output_n)))
                await session.commit()

    async def _get_last_block(self) -> Optional[int]:
        from sqlalchemy import select

        async with self._session() as session:
            result = await session.execute(select(self.LastBlock.block_number))
            return result.scalar()

    async def _get_addresses(self) -> list[str]:
        from sqlalchemy import select

        async with self._session() as session:
            result = await session.execute(select(self.Address.address))
            return {row[0] for row in result.fetchall()}
