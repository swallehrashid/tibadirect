"""Everything that talks to PayPal: the REST client, its offline fake, webhooks and settlement
adapters. Services call the client through ``paypal_client.get_client()`` and never import
``requests`` themselves."""
