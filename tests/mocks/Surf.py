import re
import os
import sys
import aiohttp
import logging


from tonclient.types import DeploySet, CallSet, Signer, ParamsOfSign, \
    ParamsOfEncodeMessageBody, ParamsOfProcessMessage, ParamsOfEncodeMessage

from torauth.utils import credit, calc_address, base64_to_hex, process_message

log = logging.getLogger(__name__)


##
# This is a Surf mock.
# This code will be completely rewritten when DeBot is ready
##

# To decode QR code external public API is used
external_api = 'https://zxing.org/w/decode'


class Surf:

    def __init__(self, config, root_address):
        self.root_address = root_address
        self.cfg = config

    async def get_public_key(self):
        return self.keys.public

    def sign_random(self, random):
        return self.cfg.client.crypto.sign(params=ParamsOfSign(
            unsigned=random,
            keys=self.keys
        ))

    async def deploy_wallet(self):
        self.keys = await self.cfg.client.crypto.generate_random_sign_keys()
        signer = Signer.Keys(self.keys)

        address = await calc_address(
            client=self.cfg.client,
            abi=self.cfg.multisig_abi,
            signer=signer,
            deploy_set=DeploySet(tvc=self.cfg.multisig_tvc)
        )

        await credit(self.cfg, address, self.cfg.multisig_initial_value)

        log.debug('Deploying contract to {}'.format(address))

        await process_message(
            client=self.cfg.client,
            params=ParamsOfProcessMessage(
                message_encode_params=ParamsOfEncodeMessage(
                    abi=self.cfg.multisig_abi,
                    signer=signer,
                    address=address,
                    deploy_set=DeploySet(tvc=self.cfg.multisig_tvc),
                    call_set=CallSet(
                        function_name='constructor',
                        input={
                            'owners': ['0x' + self.keys.public],
                            'reqConfirms': 1
                        },
                    )
                ),
                send_events=False
            ))
        self.address = address

    async def send_message_to_blockchain(self, signed_random):
        log.debug('Sending message to ROOT contract')
        params = ParamsOfEncodeMessageBody(
            abi=self.cfg.root_interface_abi,
            call_set=CallSet(
                function_name='sign',
                input={
                    'signed_random': base64_to_hex(signed_random.signed),
                    'signature':  base64_to_hex(signed_random.signature),
                    'public_key': self.keys.public
                }
            ),
            is_internal=True,
            signer=Signer.NoSigner()
        )

        payload = await self.cfg.client.abi.encode_message_body(params=params)

        await process_message(
            client=self.cfg.client,
            params=ParamsOfProcessMessage(
                message_encode_params=ParamsOfEncodeMessage(
                    abi=self.cfg.multisig_abi,
                    signer=Signer.Keys(self.keys),
                    address=self.address,
                    call_set=CallSet(
                        function_name='sendTransaction',
                        input={
                            'dest': self.root_address,
                            'value': 1000000000,
                            'bounce': False,
                            'flags': 3,
                            'payload': payload.body
                        }

                    )
                ),
                send_events=False
            ))

    async def send_qr_code(self, qr_code):
        async with aiohttp.ClientSession() as session:

            # To decode QR code we use external public API
            params = {'u': 'data:image/png;base64,{}'.format(qr_code)}
            async with session.get(external_api, params=params) as response:
                log.debug('Status: {}'.format(response.status))
                if response.status == 200:
                    # Surf got `random` from QR code
                    html = await response.text()
                    random = self._extract_random(html)

                    # Lets sign it and send signed_random to blockchain
                    signed_random = await self.sign_random(random)
                    await self.send_message_to_blockchain(signed_random)
                    log.debug('Message sent!')
                else:
                    log.critical('Public QR decoder service is not available')
                    sys.exit(1)

    # Unfortunately, API sends the answer in html format only,
    # so we need to parse it

    def _extract_random(self, html):
        tokenized = re.split('<[^>]+>', html)
        res = list(filter(lambda x: self.cfg.deep_link_url in x, tokenized))
        random = res[0].split(self.cfg.deep_link_url)[-1].strip()
        log.debug('Extracted from QR code random: {}'.format(random))
        return random
