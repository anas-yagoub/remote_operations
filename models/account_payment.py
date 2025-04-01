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
    no_allow_sync = fields.Boolean("Not Allow Sync")
    remote_source_line_id = fields.Integer(string="Remote Source Line ID", copy=False)  # Store the source statement line ID
    
    @api.model
    def send_internal_transfer_payment_to_remote(self):
        """Sync internal transfer payments to the remote Odoo 18 database as bank statement lines."""
        config_parameters = self.env['ir.config_parameter'].sudo()
        remote_type = config_parameters.get_param('remote_operations.remote_type')
        if remote_type != 'Branch Database':
            _logger.info("Database is not configured as 'Branch Database'. Skipping sync.")
            return

        # Retrieve remote server settings
        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        if not all([url, db, username, password]):
            raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

        try:
            # Connect to the remote server
            common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

            # Search for internal transfer payments to sync (batch processing)
            start_date = date(2024, 7, 1).isoformat()
            payments = self.search([
                ('payment_posted_to_remote', '=', False),
                ('no_allow_sync', '=', False),
                ('is_internal_transfer', '=', True),
                ('date', '>=', start_date),
                ('state', '=', 'posted'),
            ], limit=10, order='date asc')  # Process in batches of 50

            if not payments:
                _logger.info("No internal transfer payments to sync.")
                return

            for payment in payments:
                try:
                    # Prepare data for the bank statement lines
                    _logger.info("payment Line Data for ID ***************** %s: %s", payment.id)
                    payment_line_data = payment._prepare_bank_statement_line_data(models, db, uid, password)
                    _logger.info("Source Line Data for ID ***********************8%s: %s", payment.id, payment_line_data)

                    # Create the source statement line (outgoing)
                    payment_line_id = models.execute_kw(db, uid, password, 'account.bank.statement.line', 'create', [payment_line_data])
                    _logger.info("Created source statement line in remote database: ID %s", payment_line_id)

                    # Update the local payment
                    payment.write({
                        'payment_posted_to_remote': True,
                        'remote_source_line_id': payment_line_id,
                        'failed_to_sync': False,
                    })
                    # if payment.move_id:
                    #     payment.move_id.write({'posted_to_remote': True})
                    payment.message_post(body=f"Internal transfer synced to remote database: Source Line ID {payment_line_id}")

                except Exception as e:
                    payment.write({'failed_to_sync': True})
                    _logger.error("Error syncing payment ID %s: %s", payment.id, str(e))
                    payment.message_post(body=f"Error syncing payment ID {payment.id}: {str(e)}")

        except Exception as e:
            raise ValidationError(f"Error connecting to remote server: {str(e)}")
        
    def _prepare_bank_statement_line_data(self, models, db, uid, password):
        """Prepare data for the source and destination bank statement lines in Odoo 18."""
        amount = -self.amount if self.payment_type == 'outbound' else self.amount
        currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', self.currency_id)
        payment_data = {
            'journal_id': self._map_journal_to_remote_company(models, db, uid, password, self.journal_id),
            'currency_id': currency_id  or None,
            'amount': amount or None,
            'date': self.date.isoformat() if self.date else False,
            'payment_ref': self.ref or f"Internal Transfer Out - {self.name}" or  None,
            'company_id': self._map_branch_to_remote_company(models, db, uid, password, self.branch_id, self.company_id) or None,
        }
        return payment_data

    # def _prepare_bank_statement_line_data(self, models, db, uid, password):
    #     """Prepare data for the source and destination bank statement lines in Odoo 18."""
    #     # Map journals and company
    #     source_journal_id = self._get_remote_id(models, db, uid, password, 'account.journal', 'name', self.journal_id.name)
    #     dest_journal_id = self._get_remote_id(models, db, uid, password, 'account.journal', 'name', self.destination_journal_id.name)
    #     company_id = self._map_branch_to_remote_company(models, db, uid, password, self.branch_id, self.company_id)
    #     currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', self.currency_id)


    #     # Prepare the source statement line (outgoing)
    #     source_line_data = {
    #         'journal_id': source_journal_id,
    #         'company_id': company_id,
    #         'currency_id': currency_id or False,
    #         'amount': -self.amount,  # Outgoing (negative)
    #         'date': self.date.isoformat() if self.date else False,
    #         'payment_ref': self.ref or f"Internal Transfer Out - {self.name}",
    #     }

    #     # Prepare the destination statement line (incoming)
    #     dest_line_data = {
    #         'journal_id': dest_journal_id,
    #         'company_id': company_id,
    #         'currency_id': currency_id or False,
    #         'amount': self.amount,  # Incoming (positive)
    #         'date': self.date.isoformat() if self.date else False,
    #         'payment_ref': self.ref or f"Internal Transfer In - {self.name}",
    #     }

    #     return source_line_data, dest_line_data
    
    
    
    
        # Map journals and company
        # source_journal_id = self._get_remote_id(models, db, uid, password, 'account.journal', 'name', self.journal_id.name)
        # dest_journal_id = self._get_remote_id(models, db, uid, password, 'account.journal', 'name', self.destination_journal_id.name)
        # company_id = self._map_branch_to_remote_company(models, db, uid, password, self.branch_id, self.company_id)
        # currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', self.currency_id)


        # # Prepare the source statement line (outgoing)
        # source_line_data = {
        #     'journal_id': source_journal_id,
        #     'company_id': company_id,
        #     'currency_id': currency_id or False,
        #     'amount': -self.amount,  # Outgoing (negative)
        #     'date': self.date.isoformat() if self.date else False,
        #     'payment_ref': self.ref or f"Internal Transfer Out - {self.name}",
        # }

        # # Prepare the destination statement line (incoming)
        # dest_line_data = {
        #     'journal_id': dest_journal_id,
        #     'company_id': company_id,
        #     'currency_id': currency_id or False,
        #     'amount': self.amount,  # Incoming (positive)
        #     'date': self.date.isoformat() if self.date else False,
        #     'payment_ref': self.ref or f"Internal Transfer In - {self.name}",
        # }

        # return source_line_data, dest_line_data


    
    
    def action_sync_payment_to_remote_manual(self):
        """ Manually sync selected account payments to a remote Odoo server """
        config_parameters = self.env['ir.config_parameter'].sudo()

        remote_type = config_parameters.get_param('remote_operations.remote_type')
        if remote_type != 'Branch Database':
            raise UserError(_("This database is not configured as 'Branch Database'. Sync is not required."))

        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        if not all([url, db, username, password]):
            raise UserError(_("Remote server settings must be fully configured (URL, DB, Username, Password)"))

        try:
            common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
            uid = common.authenticate(db, username, password, {})
            if not uid:
                raise UserError(_("Authentication failed with remote server."))

            models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)
            start_date = fields.Date.to_date('2024-07-01')

            for payment in self:
                try:
                    if payment.payment_posted_to_remote:
                        continue  # Skip already processed records

                    if payment.is_internal_transfer:
                        continue  # Skip internal transfers

                    if payment.date < start_date:
                        continue  # Skip payments before the sync date

                    # Ensure partner exists remotely
                    partner_id = payment._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name', payment.partner_id)
                    if not partner_id and payment.partner_id:
                        partner_id = payment._create_remote_partner(models, db, uid, password, payment.partner_id)

                    payment_data = payment._prepare_payment_data(models, db, uid, password)

                    new_payment_id = models.execute_kw(db, uid, password, 'account.payment', 'create', [payment_data])

                    models.execute_kw(db, uid, password, 'account.payment', 'action_post', [[new_payment_id]])

                    payment.write({
                        'payment_posted_to_remote': True,
                        'remote_id': new_payment_id
                    })
                    self.env.cr.commit()

                    _logger.info(f"Successfully synced Payment ID {payment.id} to remote server with ID {new_payment_id}")

                except Exception as payment_error:
                    # payment.write({'failed_to_sync': True})
                    _logger.error(f"Error syncing Payment ID {payment.id}: {str(payment_error)}")
                    payment.message_post(body=f"Error syncing Payment ID {payment.id}: {str(payment_error)}")
                    self.env.cr.rollback()

        except Exception as e:
            _logger.error(f"Critical error in remote payment sync: {str(e)}")
            raise UserError(_("Error while sending payment data to remote server: ") + str(e))
            # sefl.message_post(body=f"Error syncing Payment ID {payment.id}: {str(payment_error)}")


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
                ('no_allow_sync', '=', False),
                ('is_internal_transfer', '=', False),
                ('date', '>=', start_date),
                ('state', '=', 'posted'),
                # ('remote_id', '=', 0)
            ], limit=10, order='date asc')
            
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
                    payment.message_post(body="Payment Has been created ")


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
        
    
    # @api.model     
    # def send_internal_transfer_payment_to_remote(self):
    #     """Send the internal transfer payment to the remote system."""
    #     config_parameters = self.env['ir.config_parameter'].sudo()
    #     remote_type = config_parameters.get_param('remote_operations.remote_type')
    #     if remote_type != 'Branch Database':
    #         _logger.info("Database is not configured as 'Branch Database'. Skipping sending payments to remote.")
    #         return

    #     url = config_parameters.get_param('remote_operations.url')
    #     db = config_parameters.get_param('remote_operations.db')
    #     username = config_parameters.get_param('remote_operations.username')
    #     password = config_parameters.get_param('remote_operations.password')

    #     if not all([url, db, username, password]):
    #         raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

    #     try:
    #         common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url), allow_none=True)
    #         uid = common.authenticate(db, username, password, {})
    #         models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url), allow_none=True)

    #         start_date = date(2024, 7, 1).isoformat()
    #         payments = self.search([
    #             ('payment_posted_to_remote', '=', False),
    #             ('no_allow_sync', '=', False),
    #             ('is_internal_transfer', '=', True),
    #             ('date', '>=', start_date),
    #             ('state', '=', 'posted'),
    #             # ('remote_id', '=', 0)
    #         ], limit=1, order='date asc')
            
    #         for payment in payments:
    #             try:
    #                 move_id = payment.move_id
    #                 payment_data = payment._prepare_internal_transfer_payment_data(models, db, uid, password)
    #                 _logger.info("Payment Data: %s", payment_data, payment)
    #                 new_payment_id = models.execute_kw(db, uid, password, 'account.bank.statement.line', 'create', [payment_data])
    #                 payment.write({'payment_posted_to_remote': True, 'remote_id': new_payment_id})
    #                 if payment.move_id:
    #                     payment.move_id.write({'posted_to_remote': True})  
    #                 # models.execute_kw(db, uid, password, 'account.payment', 'action_post', [[new_payment_id]])
    #                 _logger.info("Payment Has been created *********************: %s", new_payment_id)
    #                 payment.message_post(body="Payment Has been created ")


    #             except Exception as e:
    #                 # payment.write({'failed_to_sync': True})
    #                 _logger.error("Error processing payment ID %s: %s", payment.id, str(e))
    #                 payment.message_post(body="Error processing payment ID {}: {}".format(payment.id, str(e)))


    #     except Exception as e:
    #         raise ValidationError("Error while sending payment data to remote server: {}".format(e))
        
    # def _prepare_internal_transfer_payment_data(self, models, db, uid, password):
    #     """Prepare the payment data for the remote server."""
    #     # partner_id = self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name', self.partner_id)
    #     # if not partner_id and self.partner_id:
    #     #     # Create the partner in the remote system if it doesn't exist
    #     #     partner_id = self._create_remote_partner(models, db, uid, password, self.partner_id)
    #     journal_id = self._get_remote_id(models, db, uid, password, 'account.journal', 'name', self.journal_id.name)
    #     currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', self.currency_id)
    #     payment_data = {
    #         # 'partner_id': partner_id,
    #         'journal_id': self._map_journal_to_remote_company(models, db, uid, password, self.journal_id),
    #         'currency_id': currency_id  or None,
    #         'amount': self.amount or None,
    #         'date': self.date  or None,
    #         # 'payment_type': self.payment_type  or None,
    #         # 'partner_type': self.partner_type  or None,
    #         'payment_ref': self.ref or None,
    #         'company_id': self._map_branch_to_remote_company(models, db, uid, password, self.branch_id, self.company_id) or None,
    #         # 'payment_method_line_id': self._get_remote_id(models, db, uid, password, 'account.payment.method.line', 'name', self.payment_method_id),
    #     }
    #     return payment_data

   
    

    
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


    