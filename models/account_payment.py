# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import requests, json, base64
from datetime import datetime, date
from odoo.exceptions import ValidationError, UserError
import xmlrpc.client
from pytz import timezone
import logging
_logger = logging.getLogger(__name__)



class AccountPayment(models.Model):
    _inherit = 'account.payment'

    payment_posted_to_remote = fields.Boolean("Payment Posted to remote", copy=False)
    failed_to_sync = fields.Boolean("Failed To Sync", copy=False)
    remote_id = fields.Integer(string="Remote Id", copy=False)



    # @api.model
    # def action_send_payments_to_remote_cron(self):
    #     """Cron job to send payments to the remote server."""
    #     start_date = date(2024, 7, 1).isoformat()
    #     payments = self.search([('payment_posted_to_remote', '=', False),('is_internal_transfer', '=', False), ('state', '=', 'posted'), ('date', '>=', start_date)], order='date asc', limit=10)
    #     for payment in payments:
    #         try:
    #             payment.send_payment_to_remote()
    #             self.env.cr.commit()  # Commit each payment individually
    #         except Exception as e:
    #             _logger.error("Error processing payment ID %s: %s", payment.id, str(e))
    #             self.env.cr.rollback()  # Rollback only for the failed transaction
    @api.model     
    def send_payment_to_remote(self):
        """Send the payment to the remote system."""
        config_parameters = self.env['ir.config_parameter'].sudo()
        remote_type = config_parameters.get_param('remote_operations.remote_type')
        if remote_type != 'Branch Database':
            _logger.info("Database is not configured as 'Branch Database'. Skipping sending payments to remote.")
            return

        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        if not all([url, db, username, password]):
            raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

        try:
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url), allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url), allow_none=True)

            start_date = date(2024, 7, 1).isoformat()
            payments = self.search([
                ('payment_posted_to_remote', '=', False),
                # ('failed_to_sync', '=', False),
                ('is_internal_transfer', '=', False),
                ('date', '>=', start_date),
                ('state', '=', 'posted'),
                ('remote_id', '=', 0)
            ], limit=10, order='date asc')
            
            print("********************************************payments", payments)

            for payment in payments:
                try:
                    move_id = payment.move_id
                    payment_data = payment._prepare_payment_data(models, db, uid, password)
                    _logger.info("Payment Data: %s", payment_data)
                    new_payment_id = models.execute_kw(db, uid, password, 'account.payment', 'create', [payment_data])
                    payment.write({'payment_posted_to_remote': True, 'remote_id': new_payment_id})
                    if payment.move_id:
                        payment.move_id.write({'posted_to_remote': True})  
                    models.execute_kw(db, uid, password, 'account.payment', 'action_post', [[new_payment_id]])
                    _logger.info("Payment Has been created *********************: %s", new_payment_id)

                except Exception as e:
                    # payment.write({'failed_to_sync': True})
                    _logger.error("Error processing payment ID %s: %s", payment.id, str(e))
                    payment.message_post(body="Error processing payment ID {}: {}".format(payment.id, str(e)))


        except Exception as e:
            raise ValidationError("Error while sending payment data to remote server: {}".format(e))

    def _prepare_payment_data(self, models, db, uid, password):
        """Prepare the payment data for the remote server."""
        partner_id = self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name', self.partner_id)
        if not partner_id and self.partner_id:
            # Create the partner in the remote system if it doesn't exist
            partner_id = self._create_remote_partner(models, db, uid, password, self.partner_id)
        journal_id = self._get_remote_id(models, db, uid, password, 'account.journal', 'name', self.journal_id.name)
        currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', self.currency_id)
        payment_data = {
            'partner_id': partner_id,
            'journal_id': self._map_journal_to_remote_company(models, db, uid, password, self.journal_id),
            'currency_id': currency_id  or None,
            'amount': self.amount or None,
            'date': self.date  or None,
            'payment_type': self.payment_type  or None,
            'partner_type': self.partner_type  or None,
            'memo': self.ref or None,
            'company_id': self._map_branch_to_remote_company(models, db, uid, password, self.branch_id, self.company_id) or None,
            # 'payment_method_line_id': self._get_remote_id(models, db, uid, password, 'account.payment.method.line', 'name', self.payment_method_id),
        }
        return payment_data
    
    
    
    
    def _map_branch_to_remote_company(self, models, db, uid, password, branch_id=None, company_id=None):
        """
        Map the branch or company to a remote company.

        If branch_id is not provided or is already a company object, fall back to using company_id.
        """
        remote_company_id = None
        local_company = None

        if branch_id:
            local_company = branch_id
        elif company_id:
            # Fallback to using company_id if branch_id is not provided
            local_company = company_id
        else:
            raise ValueError("Either branch_id or company_id must be provided to map to a remote company.")

        # Map to the remote company by name or another unique field
        remote_company_id = self._get_remote_id(
            models, db, uid, password,
            'res.company', 'name', local_company.name
        )

        return remote_company_id
    
    
    def _get_remote_journal_id(self, models, db, uid, password, model_name, domain=None):
        # If a domain is provided, use it to search
        if domain:
            remote_model = models.execute_kw(db, uid, password, model_name, 'search', [domain])
        else:
            raise ValueError("Domain is required to search for remote records.")
        return remote_model[0] if remote_model else None

    def _map_journal_to_remote_company(self, models, db, uid, password, journal):
        remote_journal_id = None
        if journal:
            # Get the local company linked to the journal
            local_journal = journal
            local_company_id = local_journal.company_id.id
        
            # Map to the remote company journal by name and company
            remote_journal_id = self._get_remote_journal_id(
                models, db, uid, password,
                'account.journal', 
                domain=[
                    ('name', '=', local_journal.name),
                    ('company_id', '=', local_company_id)
                ]
            )

        return remote_journal_id
    
    
    
    def _get_remote_id(self, models, db, uid, password, model, field_name, field_value):
        remote_record = models.execute_kw(
            db, uid, password, model, 'search_read', 
            [[(field_name, '=', field_value)]], 
            {'fields': ['id'], 'limit': 1}
        )
        if not remote_record:
            # Instead of raising an error, return None or handle creation here
            _logger.warning(
                "The record for model '%s' with %s '%s' was not found in the remote database.",
                model, field_name, field_value
            )
            return None  # Or choose to create the record dynamically if needed
        return remote_record[0]['id']


    # def _get_remote_id_if_set(self, models, db, uid, password, model_name, field_name, local_record):
    #     """Fetch the remote ID if the local record exists."""
    #     if not local_record:
    #         return None
    #     return self._get_remote_id(models, db, uid, password, model_name, field_name, local_record)
    
    def _get_remote_id_if_set(self, models, db, uid, password, model, field_name, field):
        if field:
            return self._get_remote_id(models, db, uid, password, model, field_name, field.name)
        return False
    
    def _create_remote_partner(self, models, db, uid, password, partner):
        """Create a partner in the remote database and return the new remote ID."""
        for rec in self:
            print("*************************************** partner data #####################")
            account_receivable_id_to_check = rec.partner_id.property_account_receivable_id.code
            account_payable_to_check = rec.partner_id.property_account_payable_id.code
            
            property_account_receivable_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_receivable_id_to_check)
            property_account_payable_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_payable_to_check)

        partner_data = {
            'name': partner.name,
            'email': partner.email,
            'phone': partner.phone,
            'mobile': partner.mobile,
            'street': partner.street,
            'city': partner.city,
            # 'state_id': self._get_remote_id_if_set(models, db, uid, password, 'res.country.state', 'name', partner.state_id),
            'country_id': self._get_remote_id_if_set(models, db, uid, password, 'res.country', 'name', partner.country_id),
            'zip': partner.zip,
            'vat': partner.vat,
            'customer_rank': partner.customer_rank,
            'supplier_rank': partner.supplier_rank,
            'property_account_receivable_id': property_account_receivable_id,
            'property_account_payable_id': property_account_payable_id,

        }
        return models.execute_kw(db, uid, password, 'res.partner', 'create', [partner_data])


# class AccountPayment(models.Model):
#     _inherit = 'account.payment'

#     payment_posted_to_remote = fields.Boolean("Payment Posted to remote")

#     @api.model
#     def action_send_payments_to_remote_cron(self):
#         """Cron job to send payments to the remote server."""
#         start_date = date(2024, 7, 1).isoformat()
#         payments = self.search([('payment_posted_to_remote', '=', False),('is_internal_transfer', '=', False), ('state', '=', 'posted'), ('date', '>=', start_date)], order='date asc', limit=10)
#         for payment in payments:
#             try:
#                 payment.send_payment_to_remote()
#                 self.env.cr.commit()  # Commit each payment individually
#             except Exception as e:
#                 _logger.error("Error processing payment ID %s: %s", payment.id, str(e))
#                 self.env.cr.rollback()  # Rollback only for the failed transaction
                
#     def send_payment_to_remote(self):
#         """Send the payment to the remote system."""
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

#             start_date = date(2024, 7, 1).isoformat()
#             payments = self.search([
#                 ('payment_posted_to_remote', '=', False),
#                 ('is_internal_transfer', '=', False),
#                 ('date', '>=', start_date),
#                 ('state', '=', 'posted')
#             ], limit=10, order='date asc')

#             for payment in payments:
#                 try:
#                     payment_data = payment._prepare_payment_data(models, db, uid, password)
#                     _logger.info("Payment Data: %s", payment_data)

#                     # existing_payment = models.execute_kw(db, uid, password, 'account.payment', 'search', [[('memo', '=', payment.ref)]])
#                     # if existing_payment:
#                     #     _logger.warning("Payment %s already exists on remote with ID %s", payment.ref, existing_payment[0])
#                     #     payment.write({'payment_posted_to_remote': True})
#                     #     continue

#                     new_payment_id = models.execute_kw(db, uid, password, 'account.payment', 'create', [payment_data])
#                     models.execute_kw(db, uid, password, 'account.payment', 'action_post', [[new_payment_id]])
#                     _logger.info("Payment Has been created *********************: %s", new_payment_id)

#                     payment.write({'payment_posted_to_remote': True})
#                     self.env.cr.commit()

#                 except Exception as e:
#                     _logger.error("Error processing payment ID %s: %s", payment.id, str(e))
#                     self.env.cr.rollback()
#         except Exception as e:
#             raise ValidationError("Error while sending payment data to remote server: {}".format(e))

#     def _prepare_payment_data(self, models, db, uid, password):
#         """Prepare the payment data for the remote server."""
#         partner_id = self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name', self.partner_id)
#         if not partner_id and self.partner_id:
#             # Create the partner in the remote system if it doesn't exist
#             partner_id = self._create_remote_partner(models, db, uid, password, self.partner_id)
#         journal_id = self._get_remote_id(models, db, uid, password, 'account.journal', 'name', self.journal_id.name)
#         currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', self.currency_id)
#         payment_data = {
#             'partner_id': partner_id or None,
#             'journal_id': self._map_journal_to_remote_company(models, db, uid, password, self.journal_id),
#             'currency_id': currency_id  or None,
#             'amount': self.amount or None,
#             'date': self.date  or None,
#             'payment_type': self.payment_type  or None,
#             'memo': self.ref or None,
#             'company_id': self._map_branch_to_remote_company(models, db, uid, password, self.branch_id, self.company_id) or None,
#             # 'payment_method_line_id': self._get_remote_id(models, db, uid, password, 'account.payment.method.line', 'name', self.payment_method_id),
#         }
#         return payment_data
    
#     def _map_branch_to_remote_company(self, models, db, uid, password, branch_id=None, company_id=None):
#         """
#         Map the branch or company to a remote company.

#         If branch_id is not provided or is already a company object, fall back to using company_id.
#         """
#         remote_company_id = None
#         local_company = None

#         if branch_id:
#             # Check if branch_id is a res.branch or res.company object
#             # if hasattr(branch_id, 'company_id'):
#             #     # branch_id is a res.branch object
#             #     local_company = branch_id.company_id
#             #     # print("*****************local_company from branch", local_company.name)
#             # else:
#                 # branch_id is already a res.company object
#             local_company = branch_id
#                 # print("*****************local_company directly from branch as company", local_company.name)
#         elif company_id:
#             # Fallback to using company_id if branch_id is not provided
#             local_company = company_id
#             # print("*****************local_company from company_id", local_company.name)
#         else:
#             raise ValueError("Either branch_id or company_id must be provided to map to a remote company.")

#         # Map to the remote company by name or another unique field
#         remote_company_id = self._get_remote_id(
#             models, db, uid, password,
#             'res.company', 'name', local_company.name
#         )
#         # print("*****************remote_company_id", remote_company_id)

#         return remote_company_id
    
#     def _get_remote_journal_id(self, models, db, uid, password, model_name, domain=None):
#         # If a domain is provided, use it to search
#         if domain:
#             remote_model = models.execute_kw(db, uid, password, model_name, 'search', [domain])
#         else:
#             raise ValueError("Domain is required to search for remote records.")
#         return remote_model[0] if remote_model else None

#     def _map_journal_to_remote_company(self, models, db, uid, password, journal):
#         remote_journal_id = None
#         if journal:
#             # Get the local company linked to the journal
#             local_journal = journal
#             local_company_id = local_journal.company_id.id

#             # Map to the remote company journal by name and company
#             remote_journal_id = self._get_remote_journal_id(
#                 models, db, uid, password,
#                 'account.journal', 
#                 domain=[
#                     ('name', '=', local_journal.name),
#                     ('company_id', '=', local_company_id)
#                 ]
#             )

#         return remote_journal_id
    
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


#     # def _get_remote_id_if_set(self, models, db, uid, password, model_name, field_name, local_record):
#     #     """Fetch the remote ID if the local record exists."""
#     #     if not local_record:
#     #         return None
#     #     return self._get_remote_id(models, db, uid, password, model_name, field_name, local_record)
    
#     def _get_remote_id_if_set(self, models, db, uid, password, model, field_name, field):
#         if field:
#             return self._get_remote_id(models, db, uid, password, model, field_name, field.name)
#         return False
    
#     def _create_remote_partner(self, models, db, uid, password, partner):
#         """Create a partner in the remote database and return the new remote ID."""
#         for rec in self:
#             print("*************************************** partner data #####################")
#             account_receivable_id_to_check = rec.partner_id.property_account_receivable_id.code
#             account_payable_to_check = rec.partner_id.property_account_payable_id.code
            
#             property_account_receivable_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_receivable_id_to_check)
#             property_account_payable_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_payable_to_check)

#         partner_data = {
#             'name': partner.name,
#             'email': partner.email,
#             'phone': partner.phone,
#             'mobile': partner.mobile,
#             'street': partner.street,
#             'city': partner.city,
#             # 'state_id': self._get_remote_id_if_set(models, db, uid, password, 'res.country.state', 'name', partner.state_id),
#             'country_id': self._get_remote_id_if_set(models, db, uid, password, 'res.country', 'name', partner.country_id),
#             'zip': partner.zip,
#             'vat': partner.vat,
#             'customer_rank': partner.customer_rank,
#             'supplier_rank': partner.supplier_rank,
#             'property_account_receivable_id': property_account_receivable_id,
#             'property_account_payable_id': property_account_payable_id,

#         }
#         return models.execute_kw(db, uid, password, 'res.partner', 'create', [partner_data])


    