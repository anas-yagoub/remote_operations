# # -*- coding: utf-8 -*-

# from odoo import models, fields, api, _
# import requests, json, base64
# from datetime import datetime, date
# from odoo.exceptions import ValidationError, UserError
# import xmlrpc.client
# from pytz import timezone
# import logging
# _logger = logging.getLogger(__name__)

# class ResCurrency(models.Model):
#     _inherit = 'res.currency'




# class ResCurrencyRate(models.Model):
#     _inherit = 'res.currency.rate'

#     posted_to_remote = fields.Boolean("Posted to remote")
#     failed_to_sync = fields.Boolean("Failed To Sync")
    
    
#     @api.model     
#     def send_currency_rate_to_remote(self):
#         config_parameters = self.env['ir.config_parameter'].sudo()
#         remote_type = config_parameters.get_param('remote_operations.remote_type')
#         if remote_type != 'Branch Database':
#             _logger.info("Database is not configured as 'Branch Database'. Skipping sending payments to remote.")
#             return

#         url = config_parameters.get_param('remote_operations.url')
#         db = config_parameters.get_param('remote_operations.db')
#         username = config_parameters.get_param('remote_operations.username')
#         password = config_parameters.get_param('remote_operations.password')

#         if not all([url, db, username, password]):
#             raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

#         try:
#             common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url), allow_none=True)
#             uid = common.authenticate(db, username, password, {})
#             models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url), allow_none=True)

#             currency_rate = self.search([
#                 ('posted_to_remote', '=', False),
#                 ('failed_to_sync', '=', False),
#                 ('currency_id.symbol', '=', '$'),
#             ], limit=1)

#             for rate in currency_rate:
#                 try:
#                     currency_rate_data = rate._prepare_rate_data(models, db, uid, password)
#                     _logger.info("rate Data: %s", rate)
#                     new_rate_id = models.execute_kw(db, uid, password, 'res.currency.rate', 'create', [currency_rate_data])
#                     rate.write({'posted_to_remote': True})
#                     _logger.info("rate Data has been created: %s", new_rate_id)
                    
#                 except Exception as e:
#                     rate.write({'failed_to_sync': True})
#                     _logger.error("Error processing rate ID %s: %s", rate.id, str(e))

#         except Exception as e:
#             raise ValidationError("Error while sending rate data to remote server: {}".format(e))

#     def _prepare_rate_data(self, models, db, uid, password):
#         """Prepare the rate data for the remote server."""
#         rate_data = {
#             'name': self.name,
#             'company_id': self._map_to_remote_company(models, db, uid, password, self.company_id) or None,
#             'company_rate': self.company_rate,
#             'inverse_company_rate': self.inverse_company_rate ,
#             'rate': self.rate,
#             'currency_id': self.currency_id.id,
#         }
#         print("**************************rate_data", rate_data)
#         return rate_data
    
#     def _map_to_remote_company(self, models, db, uid, password, company_id=None):
#         """
#         Map the branch or company to a remote company.

#         If branch_id is not provided or is already a company object, fall back to using company_id.
#         """
#         remote_company_id = None
#         local_company = None
        
#         if company_id:
#             local_company = company_id
#         else:
#             raise ValueError("Either branch_id or company_id must be provided to map to a remote company.")

#         # Map to the remote company by name or another unique field
#         remote_company_id = self._get_remote_id(
#             models, db, uid, password,
#             'res.company', 'name', local_company.name
#         )

#         return remote_company_id

    
#     def _get_remote_id(self, models, db, uid, password, model, field_name, field_value):
#         remote_record = models.execute_kw(
#             db, uid, password, model, 'search_read', 
#             [[(field_name, '=', field_value)]], 
#             {'fields': ['id'], 'limit': 1}
#         )
#         if not remote_record:
#             # Instead of raising an error, return None or handle creation here
#             _logger.warning(
#                 "The record for model '%s' with %s '%s' was not found in the remote database.",
#                 model, field_name, field_value
#             )
#             return None  # Or choose to create the record dynamically if needed
#         return remote_record[0]['id']


# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
import xmlrpc.client
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

class ResCurrencyRate(models.Model):
    _inherit = 'res.currency.rate'

    posted_to_remote = fields.Boolean("Posted to Remote", default=False)
    failed_to_sync = fields.Boolean("Failed to Sync", default=False)

    @api.model
    def send_currency_rate_to_remote(self):
        """Send unsynchronized currency rates to the remote server."""
        config_parameters = self.env['ir.config_parameter'].sudo()
        remote_type = config_parameters.get_param('remote_operations.remote_type')

        if remote_type != 'Branch Database':
            _logger.info("This database is not configured as a 'Branch Database'. Skipping synchronization.")
            return

        # Retrieve remote server configurations
        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        if not all([url, db, username, password]):
            raise ValidationError(_("Remote server settings are incomplete. Please configure URL, DB, Username, and Password."))

        try:
            # Authenticate with the remote server
            common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

            # Find unsynchronized currency rates for the $ symbol
            currency_rates = self.search([
                ('posted_to_remote', '=', False),
                ('failed_to_sync', '=', False),
                ('currency_id.symbol', '=', '$'),
            ], limit=10)

            for rate in currency_rates:
                try:
                    # Prepare data for synchronization
                    currency_rate_data = rate._prepare_rate_data(models, db, uid, password)
                    _logger.info("Preparing rate data for synchronization: %s", currency_rate_data)

                    # Send data to the remote server
                    new_rate_id = models.execute_kw(db, uid, password, 'res.currency.rate', 'create', [currency_rate_data])
                    rate.write({'posted_to_remote': True})
                    _logger.info("Currency rate successfully synchronized. Remote ID: %s", new_rate_id)

                except Exception as sync_error:
                    rate.write({'failed_to_sync': True})
                    _logger.error("Failed to synchronize rate ID %s: %s", rate.id, str(sync_error))

        except Exception as connection_error:
            raise ValidationError(_("Error connecting to remote server: %s") % connection_error)

    def _prepare_rate_data(self, models, db, uid, password):
        """Prepare currency rate data for the remote server."""
        return {
            'name': self.name,
            'company_id': self._map_to_remote_company(models, db, uid, password, self.company_id),
            'rate': self.rate,
            'currency_id': self._get_remote_currency_id(models, db, uid, password, self.currency_id),
            'company_rate': self.company_rate,
            'inverse_company_rate': self.inverse_company_rate ,
        }

    def _map_to_remote_company(self, models, db, uid, password, company_id):
        """Map the local company to the corresponding remote company."""
        if not company_id:
            raise ValidationError(_("Company is required for mapping."))

        return self._get_remote_id(models, db, uid, password, 'res.company', 'name', company_id.name)

    def _get_remote_currency_id(self, models, db, uid, password, currency_id):
        """Map the local currency to the corresponding remote currency."""
        if not currency_id:
            raise ValidationError(_("Currency is required for mapping."))

        return self._get_remote_id(models, db, uid, password, 'res.currency', 'name', currency_id.name)

    def _get_remote_id(self, models, db, uid, password, model, field_name, field_value):
        """Retrieve the remote record ID by searching for a matching field value."""
        remote_record = models.execute_kw(
            db, uid, password, model, 'search_read',
            [[(field_name, '=', field_value)]],
            {'fields': ['id'], 'limit': 1}
        )
        if not remote_record:
            _logger.warning("No matching record found in remote for %s with %s = %s.", model, field_name, field_value)
            return None
        return remote_record[0]['id']
