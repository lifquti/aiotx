import os
import sys

import pytest
from conftest import vcr_c

from aiotx.clients import AioTxLTCClient
from aiotx.exceptions import InsufficientFunds

TEST_LTC_WALLET_PRIVATE_KEY = os.getenv("TEST_LTC_WALLET_PRIVATE_KEY")
assert TEST_LTC_WALLET_PRIVATE_KEY is not None, "add TEST_LTC_WALLET_PRIVATE_KEY"
TEST_LTC_ADDRESS = "tltc1qswslzcdulvlk62gdrg8wa0sw36f938h2cvtaf7"


@vcr_c.use_cassette("ltc/get_last_block.yaml")
async def test_get_last_block(ltc_public_client: AioTxLTCClient):
    block_id = await ltc_public_client.get_last_block_number()
    assert isinstance(block_id, int)


@vcr_c.use_cassette("ltc/get_block_by_number.yaml")
async def test_get_block_by_number(ltc_public_client: AioTxLTCClient):
    block = await ltc_public_client.get_block_by_number(3247846)
    assert isinstance(block, dict)

    assert block["hash"] == "4314081d2a5d8633c51799113aac1516f174d5da5793c966848ac34177fb61c9"

    tx_hashes = [tx["hash"] for tx in block["tx"]]
    assert "68e73174f7b44edad7289a771d3069ff91c8bbb30cf96ea9861a4a312c1a2dda" in tx_hashes
    assert "4676f0e42c940f827c7f3b580119c10fe282b39bdbb798dc96d9292c387b37f0" in tx_hashes


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Skipping transaction signing tests on Windows because we are not using RFC6979 from fastecdsa by default",
)
@vcr_c.use_cassette("ltc/send_transaction.yaml")
async def test_send_transaction(ltc_public_client: AioTxLTCClient):
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "55863cc61de0c6c1c87282d3d6fb03650c0fc90ed3282191c618069cbde1d525", 39000000, 0
    )

    amount = ltc_public_client.to_satoshi(0.1)
    fee = ltc_public_client.to_satoshi(0.005)
    tx_id = await ltc_public_client.send(
        TEST_LTC_WALLET_PRIVATE_KEY, "tltc1q24gng65qj3wr55878324w2eeeta4k2plfwaf54", amount, fee
    )
    assert tx_id == "a006aedf3a08f423434aa781988997a0526f9365fe228fb8934ea64bbbb9d055"
    utxo_list = await ltc_public_client.monitor._get_utxo_data(TEST_LTC_ADDRESS)
    assert len(utxo_list) == 0
    await ltc_public_client.monitor._delete_utxo("55863cc61de0c6c1c87282d3d6fb03650c0fc90ed3282191c618069cbde1d525", 0)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Skipping transaction signing tests on Windows because we are not using RFC6979 from fastecdsa by default",
)
@vcr_c.use_cassette("ltc/send_bulk.yaml")
async def test_bulk_send_transaction(ltc_public_client: AioTxLTCClient):
    fee = ltc_public_client.to_satoshi(0.005)
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "55863cc61de0c6c1c87282d3d6fb03650c0fc90ed3282191c618069cbde1d525", 30000000, 2
    )

    amount = ltc_public_client.to_satoshi(0.05) - fee / 2
    tx_id = await ltc_public_client.send_bulk(
        TEST_LTC_WALLET_PRIVATE_KEY,
        {
            "tltc1q24gng65qj3wr55878324w2eeeta4k2plfwaf54": amount,
            "tltc1qshy0jeejm4pw3ep4cedc5vlmxyz348epnk7etf": amount,
        },
        fee,
    )
    utxo_list = await ltc_public_client.monitor._get_utxo_data(TEST_LTC_ADDRESS)
    assert len(utxo_list) == 0
    assert tx_id == "64a89e7e269469c126e96d1de7b553850716c71d9081b153429ff781758a59a1"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Skipping transaction signing tests on Windows because we are not using RFC6979 from fastecdsa by default",
)
@vcr_c.use_cassette("ltc/send_from_two_utxo.yaml")
async def test_send_from_two_utxo(ltc_public_client: AioTxLTCClient):
    fee = ltc_public_client.to_satoshi(0.005)
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "a006aedf3a08f423434aa781988997a0526f9365fe228fb8934ea64bbbb9d055", 28500000, 0
    )
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "64a89e7e269469c126e96d1de7b553850716c71d9081b153429ff781758a59a1", 20000000, 0
    )
    amount = 28500000 + 20000000 - fee
    tx_id = await ltc_public_client.send(TEST_LTC_WALLET_PRIVATE_KEY, TEST_LTC_ADDRESS, amount, fee)
    utxo_list = await ltc_public_client.monitor._get_utxo_data(TEST_LTC_ADDRESS)
    assert len(utxo_list) == 0
    assert tx_id == "3fcd11698664ffea5fe00adef6bd2c1c35de66c3fafb50f9076c74ff13fea139"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Skipping transaction signing tests on Windows because we are not using RFC6979 from fastecdsa by default",
)
@vcr_c.use_cassette("ltc/send_to_legacy_address.yaml")
async def test_send_to_legacy_address(ltc_public_client: AioTxLTCClient):
    fee = ltc_public_client.to_satoshi(0.005)
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "3fcd11698664ffea5fe00adef6bd2c1c35de66c3fafb50f9076c74ff13fea139", 48000000, 0
    )
    amount = ltc_public_client.to_satoshi(0.005)
    tx_id = await ltc_public_client.send(TEST_LTC_WALLET_PRIVATE_KEY, "mq2PZs9p5ZNLbu23KLKb1tdQt1mrBJM7CX", amount, fee)
    utxo_list = await ltc_public_client.monitor._get_utxo_data(TEST_LTC_ADDRESS)
    assert len(utxo_list) == 0
    assert tx_id == "141c30ea6326ab447423465d2a7f4c3067812f06ef1e505c0443e85c06ed684a"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Skipping transaction signing tests on Windows because we are not using RFC6979 from fastecdsa by default",
)
@vcr_c.use_cassette("ltc/send_to_legacy_and_segwit_addresses.yaml")
async def test_send_to_legacy_and_segwit_address(ltc_public_client: AioTxLTCClient):
    fee = ltc_public_client.to_satoshi(0.005)
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "141c30ea6326ab447423465d2a7f4c3067812f06ef1e505c0443e85c06ed684a", 47000000, 0
    )
    amount = ltc_public_client.to_satoshi(0.005)
    tx_id = await ltc_public_client.send_bulk(
        TEST_LTC_WALLET_PRIVATE_KEY,
        {"mq2PZs9p5ZNLbu23KLKb1tdQt1mrBJM7CX": amount, "tltc1q24gng65qj3wr55878324w2eeeta4k2plfwaf54": amount},
        fee,
    )
    utxo_list = await ltc_public_client.monitor._get_utxo_data(TEST_LTC_ADDRESS)
    assert len(utxo_list) == 0
    assert tx_id == "33f67e7ac0dde523598f416f8efa5928d9e8a4a681db48f7df7d174701225dd0"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Skipping transaction signing tests on Windows because we are not using RFC6979 from fastecdsa by default",
)
@vcr_c.use_cassette("ltc/send_with_auto_fee.yaml")
async def test_send_with_auto_fee(ltc_public_client: AioTxLTCClient):
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "33f67e7ac0dde523598f416f8efa5928d9e8a4a681db48f7df7d174701225dd0", 45500000, 0
    )
    amount = ltc_public_client.to_satoshi(0.005)
    tx_id = await ltc_public_client.send(TEST_LTC_WALLET_PRIVATE_KEY, "mq2PZs9p5ZNLbu23KLKb1tdQt1mrBJM7CX", amount)
    utxo_list = await ltc_public_client.monitor._get_utxo_data(TEST_LTC_ADDRESS)
    assert len(utxo_list) == 0
    assert tx_id == "ac3c62cb37887a41235fecbc8dd22c8a8b5b74e1d2695dbc78e6d05a0cbdf2e9"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Skipping transaction signing tests on Windows because we are not using RFC6979 from fastecdsa by default",
)
@vcr_c.use_cassette("ltc/send_with_auto_fee_and_deduct_commission.yaml")
async def test_send_with_auto_fee_and_deduct_commission(ltc_public_client: AioTxLTCClient):
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    utxo_amount = ltc_public_client.to_satoshi(0.44448584)
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "487933e5f8028e9235f76dacb368efd64bb6f038989ff92b217661c192f0055f", utxo_amount, 0
    )
    amount = ltc_public_client.to_satoshi(0.0055)
    tx_id = await ltc_public_client.send(TEST_LTC_WALLET_PRIVATE_KEY, "mq2PZs9p5ZNLbu23KLKb1tdQt1mrBJM7CX", amount, deduct_fee=True)
    utxo_list = await ltc_public_client.monitor._get_utxo_data(TEST_LTC_ADDRESS)
    assert len(utxo_list) == 0
    assert tx_id == "3ed6c7a8e3b263679c47223f3e9c65721f865378e1b6799182f0607e1ebf9179"

@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Skipping transaction signing tests on Windows because we are not using RFC6979 from fastecdsa by default",
)
@vcr_c.use_cassette("ltc/bulk_send_with_auto_fee_and_deduct_commission.yaml")
async def test_bulk_send_with_auto_fee_and_deduct_commission(ltc_public_client: AioTxLTCClient):
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    utxo_amount = ltc_public_client.to_satoshi(0.43397453)
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "3f7e5e1a897b6698d551f2b576d15eda85bf1c0d2706f7cb8c4b17a75632d1d2", utxo_amount, 0
    )
    amount = ltc_public_client.to_satoshi(0.005)
    tx_id = await ltc_public_client.send_bulk(TEST_LTC_WALLET_PRIVATE_KEY, {"mq2PZs9p5ZNLbu23KLKb1tdQt1mrBJM7CX": amount,
                                                                            "tltc1qshy0jeejm4pw3ep4cedc5vlmxyz348epnk7etf": amount},
                                                                            deduct_fee=True)
    
    utxo_list = await ltc_public_client.monitor._get_utxo_data(TEST_LTC_ADDRESS)
    assert len(utxo_list) == 0
    assert tx_id == "e328d42e61f6b022c1534cd2e7e184180574172c988bea359f0b8e8734f178c6"


async def test_zero_balance_error(ltc_public_client: AioTxLTCClient):
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    amount = ltc_public_client.to_satoshi(0.005)
    with pytest.raises(InsufficientFunds) as excinfo:  
        await ltc_public_client.send(TEST_LTC_WALLET_PRIVATE_KEY, "mq2PZs9p5ZNLbu23KLKb1tdQt1mrBJM7CX", amount)
    assert str(excinfo.value) == "We have only 0 satoshi and it's 500000 at least needed to cover that transaction!"
    

async def test_not_enough_balance_error(ltc_public_client: AioTxLTCClient):
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    utxo_amount = ltc_public_client.to_satoshi(0.005)
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "3f7e5e1a897b6698d551f2b576d15eda85bf1c0d2706f7cb8c4b17a75632d1d2", utxo_amount, 0
    )
    amount = ltc_public_client.to_satoshi(0.01)
    with pytest.raises(InsufficientFunds) as excinfo:  
        await ltc_public_client.send(TEST_LTC_WALLET_PRIVATE_KEY, "mq2PZs9p5ZNLbu23KLKb1tdQt1mrBJM7CX", amount)
    assert str(excinfo.value) == "We have only 500000 satoshi and it's 1000000 at least needed to cover that transaction!"



@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Skipping transaction signing tests on Windows because we are not using RFC6979 from fastecdsa by default",
)
@vcr_c.use_cassette("ltc/send_few_single_transactions.yaml")
async def test_send_few_single_transactions(ltc_public_client: AioTxLTCClient):
    await ltc_public_client.monitor._add_new_address(TEST_LTC_ADDRESS)
    utxo_amount = ltc_public_client.to_satoshi(0.005)
    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "89596bf8624118c6c3cd8cc2ebb6a2e19bc1c97abf994de9f23b8a2aef6765c1", utxo_amount, 2
    )
    amount = ltc_public_client.to_satoshi(0.01)
    with pytest.raises(InsufficientFunds) as excinfo:  
        await ltc_public_client.send(TEST_LTC_WALLET_PRIVATE_KEY, TEST_LTC_ADDRESS, amount)
    assert str(excinfo.value) == "We have only 500000 satoshi and it's 1000000 at least needed to cover that transaction!"

    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "c2f8e76c9537e5defa921b31edeb269603b8adec025c04d8e8b0e5764b80b861", utxo_amount, 2
    )
    
    tx_id = await ltc_public_client.send(TEST_LTC_WALLET_PRIVATE_KEY, TEST_LTC_ADDRESS, amount, deduct_fee=True)
    utxo_list = await ltc_public_client.monitor._get_utxo_data(TEST_LTC_ADDRESS)
    assert len(utxo_list) == 0
    assert tx_id == "477ff66cdf3c97661c66d38a05cfae018d0358fec300a8b85bde65808e6cf8f9"

    with pytest.raises(InsufficientFunds) as excinfo:  
        await ltc_public_client.send(TEST_LTC_WALLET_PRIVATE_KEY, TEST_LTC_ADDRESS, amount)
    assert str(excinfo.value) == "We have only 0 satoshi and it's 1000000 at least needed to cover that transaction!"

    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "8117d7261873ab0bb49e195043d70693362cebe3275333678da5f738932bb3a7", utxo_amount, 2
    )

    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "ad83a8dedeb35659cfc65053021125618ebdd0f60ee3b946fda5ea77df6a0047", utxo_amount, 2
    )

    await ltc_public_client.monitor._add_new_utxo(
        TEST_LTC_ADDRESS, "a51a47a487ab537dbf263d70a4ffd4535b57f479ba792c1549c4a4e9a44fcfb2", utxo_amount, 2
    )
    second_tx_amount = ltc_public_client.to_satoshi(0.012)
    tx_id = await ltc_public_client.send(TEST_LTC_WALLET_PRIVATE_KEY, TEST_LTC_ADDRESS, second_tx_amount)
    utxo_list = await ltc_public_client.monitor._get_utxo_data(TEST_LTC_ADDRESS)
    assert len(utxo_list) == 0
    assert tx_id == "09dac3c34ce8013a074890cdc34f90e264fda192b995350b7ad6d283f7d9276d"