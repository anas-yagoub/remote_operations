# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import requests, json, base64
from datetime import datetime, date
from odoo.exceptions import ValidationError, UserError
import xmlrpc.client
from pytz import timezone
import logging
_logger = logging.getLogger(__name__)
import time



class AccountPayment(models.Model):
    _inherit = 'account.payment'

    payment_posted_to_remote = fields.Boolean("Payment Posted to remote", copy=False)
    failed_to_sync = fields.Boolean("Failed To Sync", copy=False)
    remote_id = fields.Integer(string="Remote Id", copy=False)
    no_allow_sync = fields.Boolean("Not Allow Sync")

    
    @api.model
    def send_internal_transfer_payment_to_remote(self):
        """Send the internal transfer payment to the remote system and reconcile it."""
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
            common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

            start_date = date(2025, 5, 1).isoformat()
            payments = self.search([
                ('payment_posted_to_remote', '=', False),
                ('no_allow_sync', '=', False),
                ('is_internal_transfer', '=', True),
                ('date', '>=', start_date),
                ('state', '=', 'posted'),
                ('payment_type', '=', 'outbound'),
            ], limit=5, order='date asc')

            for payment in payments:
                try:
                    if payment.remote_id:
                        existing_payment_ids = models.execute_kw(db, uid, password, 'bank.statement.line.custom', 'search', [[('id', '=', payment.remote_id)]])
                        if existing_payment_ids:
                            _logger.info(f"Skipping Move ID {payment.id}: Already exists in remote with ID {payment.remote_id}")
                            continue
                        
                    payment_data = payment._prepare_internal_transfer_payment_data(models, db, uid, password)
                    _logger.info("Sending Payment Data: %s", payment_data)

                    outbound_payment_id = models.execute_kw(db, uid, password, 
                                                            'bank.statement.line.custom', 'create', [payment_data])

                    payment.write({'payment_posted_to_remote': True, 'remote_id': outbound_payment_id})
                    _logger.info("Outbound Payment Created on Remote: %s", outbound_payment_id)

                    # payment._reconcile_internal_transfer_payment(models, db, uid, password, outbound_payment_id)

                except Exception as e:
                    payment.write({'failed_to_sync': True})
                    _logger.error("Error processing payment ID %s: %s", payment.id, str(e))
                    payment.message_post(body="Error processing payment ID {}: {}".format(payment.id, str(e)))

        except Exception as e:
            raise ValidationError(f"Error while sending payment data to remote server: {e}")

    def _prepare_internal_transfer_payment_data(self, models, db, uid, password):
        """Prepare the outbound payment data for Odoo 18."""
        currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', self.currency_id)
        
        return {
            'name': self.name,
            'journal_id': self._map_journal_to_remote_company(models, db, uid, password, self.journal_id),
            'currency_id': currency_id or None,
            'amount': self.amount, 
            'payment_type': self.payment_type,
            'date': self.date.isoformat() if self.date else None,
            'payment_ref': self.ref or self.name or None,
            'destination_journal_id': self._map_journal_to_remote_company(models, db, uid, password, self.destination_journal_id),
            'company_id': self._map_branch_to_remote_company(models, db, uid, password, self.branch_id, self.company_id) or 
            self._map_remote_company(models, db, uid, password, self.company_id),
        }
    
    def _reconcile_internal_transfer_payment(self, models, db, uid, password, outbound_payment_id):
        """Reconcile outbound and inbound separately with liquidity handling."""
        for rec in self:
            currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', self.currency_id)
            # Create inbound payment in destination journal
            inbound_statement_vals = {
                'journal_id': self._map_journal_to_remote_company(models, db, uid, password, self.destination_journal_id),
                'currency_id': currency_id or None,
                'amount': rec.amount,  # Inbound payment is positive
                'date': rec.date.isoformat() if rec.date else None,
                'payment_ref': f"Internal Transfer from {rec.journal_id.name}",
                'company_id': self._map_branch_to_remote_company(models, db, uid, password, self.branch_id, self.company_id) or None,
            }
            _logger.info("Inbound Payment Data: %s", inbound_statement_vals)

            inbound_payment_id = models.execute_kw(db, uid, password, 
                                                'account.bank.statement.line', 'create', [inbound_statement_vals])
            # rec.write({'paired_internal_transfer_payment_id.payment_posted_to_remote': True, 'paired_internal_transfer_payment_id.remote_id': inbound_payment_id})
            rec.paired_internal_transfer_payment_id.write({
                'remote_id': inbound_payment_id,
                'payment_posted_to_remote': True,
            })
            _logger.info("Inbound Payment Created on Remote: %s", inbound_payment_id)

            # Fetch company liquidity transfer account from remote Odoo 18
            remote_company_id = self._map_branch_to_remote_company(models, db, uid, password, self.branch_id, self.company_id)
            transfer_liquidity_account = models.execute_kw(
                db, uid, password, 'res.company', 'search_read',
                [[('id', '=', remote_company_id)]],
                {'fields': ['transfer_account_id']}
            )

            if transfer_liquidity_account and transfer_liquidity_account[0].get('transfer_account_id'):
                transfer_liquidity_account_id = transfer_liquidity_account[0]['transfer_account_id'][0]
                _logger.info("Transfer Liquidity Account ID from Remote: %s", transfer_liquidity_account_id)
            else:
                raise ValidationError(_("Liquidity Transfer Account is not configured in Odoo 18!"))

            # Process outbound payment
            outbound_statement = models.execute_kw(db, uid, password, 'account.bank.statement.line', 'search_read',
                                                [[('id', '=', outbound_payment_id)]], 
                                                {'fields': ['journal_id', 'move_id']})[0]
            move_id = outbound_statement['move_id'][0] if outbound_statement['move_id'] else False
            journal_id = outbound_statement['journal_id'][0] if outbound_statement['journal_id'] else False  # Fixed typo here

            if not move_id or not journal_id:
                raise ValidationError(_("Outbound statement line %s has no move or journal!") % outbound_payment_id)

            # Fetch suspense account from journal
            journal_data = models.execute_kw(db, uid, password, 'account.journal', 'read', [journal_id], 
                                            {'fields': ['suspense_account_id', 'name']})[0]
            suspense_account_id = journal_data['suspense_account_id'][0] if journal_data['suspense_account_id'] else False
            journal_name = journal_data['name']

            if not suspense_account_id:
                raise ValidationError(_("No suspense account defined for journal %s!") % journal_name)

            # Find the suspense line in the move
            suspense_lines = models.execute_kw(db, uid, password, 'account.move.line', 'search_read', [[
                ('move_id', '=', move_id),
                ('account_id', '=', suspense_account_id),
            ]], {'fields': ['id']})

            if not suspense_lines:
                raise ValidationError(_("No suspense line found in move for outbound payment %s!") % outbound_payment_id)

            suspense_line_id = suspense_lines[0]['id']

            # Update the outbound move with try-except for button_draft
            try:
                models.execute_kw(db, uid, password, 'account.move', 'button_draft', [[move_id]])
                _logger.info("Outbound move %s set to draft", move_id)
            except Exception as e:
                _logger.warning("button_draft failed for move %s: %s. Proceeding anyway.", move_id, str(e))

            models.execute_kw(db, uid, password, 'account.move.line', 'write', [[suspense_line_id], {
                'account_id': transfer_liquidity_account_id,
                'name': f"Internal Transfer from {journal_name}",
            }])
            _logger.info("Outbound suspense line %s updated to transfer account %s", suspense_line_id, transfer_liquidity_account_id)

            try:
                models.execute_kw(db, uid, password, 'account.move', 'action_post', [[move_id]])
                _logger.info("Outbound move %s posted", move_id)
            except Exception as e:
                _logger.error("Failed to post outbound move %s: %s", move_id, str(e))
                raise

            # Process inbound payment
            inbound_statement = models.execute_kw(db, uid, password, 'account.bank.statement.line', 'search_read',
                                                [[('id', '=', inbound_payment_id)]], 
                                                {'fields': ['journal_id', 'move_id']})[0]
            move_id = inbound_statement['move_id'][0] if inbound_statement['move_id'] else False
            journal_id = inbound_statement['journal_id'][0] if inbound_statement['journal_id'] else False

            if not move_id or not journal_id:
                raise ValidationError(_("Inbound statement line %s has no move or journal!") % inbound_payment_id)

            # Fetch suspense account from journal
            journal_data = models.execute_kw(db, uid, password, 'account.journal', 'read', [journal_id], 
                                            {'fields': ['suspense_account_id', 'name']})[0]
            suspense_account_id = journal_data['suspense_account_id'][0] if journal_data['suspense_account_id'] else False
            journal_name = journal_data['name']

            if not suspense_account_id:
                raise ValidationError(_("No suspense account defined for journal %s!") % journal_name)

            # Find the suspense line in the move
            suspense_lines = models.execute_kw(db, uid, password, 'account.move.line', 'search_read', [[
                ('move_id', '=', move_id),
                ('account_id', '=', suspense_account_id),
            ]], {'fields': ['id']})

            if not suspense_lines:
                raise ValidationError(_("No suspense line found in move for inbound payment %s!") % inbound_payment_id)

            suspense_line_id = suspense_lines[0]['id']

            # Update the inbound move with try-except for button_draft
            try:
                models.execute_kw(db, uid, password, 'account.move', 'button_draft', [[move_id]])
                _logger.info("Inbound move %s set to draft", move_id)
            except Exception as e:
                _logger.warning("button_draft failed for move %s: %s. Proceeding anyway.", move_id, str(e))

            models.execute_kw(db, uid, password, 'account.move.line', 'write', [[suspense_line_id], {
                'account_id': transfer_liquidity_account_id,
                'name': f"Internal Transfer to {rec.destination_journal_id.name}",
            }])
            _logger.info("Inbound suspense line %s updated to transfer account %s", suspense_line_id, transfer_liquidity_account_id)

            try:
                models.execute_kw(db, uid, password, 'account.move', 'action_post', [[move_id]])
                _logger.info("Inbound move %s posted", move_id)
            except Exception as e:
                _logger.error("Failed to post inbound move %s: %s", move_id, str(e))
                raise

            _logger.info("Payments marked as reconciled: Outbound %s - Inbound %s", outbound_payment_id, inbound_payment_id)
        
        
        
    
    
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

            start_date = date(2025, 5, 1).isoformat()
            payments = self.search([
                ('payment_posted_to_remote', '=', False),
                ('no_allow_sync', '=', False),
                ('is_internal_transfer', '=', False),
                ('date', '>=', start_date),
                ('state', '=', 'posted'),
                # ('remote_id', '=', 0)
            ], limit=5, order='date asc')
            
            for payment in payments:
                try:
                    if payment.remote_id:
                        existing_payment_ids = models.execute_kw(db, uid, password, 'account.payment.custom', 'search', [[('id', '=', payment.remote_id)]])
                        if existing_payment_ids:
                            _logger.info(f"Skipping Payment ID {payment.id}: Already exists in remote with ID {payment.remote_id}")
                            continue
                        
                    move_id = payment.move_id
                    payment_data = payment._prepare_payment_data(models, db, uid, password)
                    _logger.info("Payment Data: %s", payment_data)
                    new_payment_id = models.execute_kw(db, uid, password, 'account.payment.custom', 'create', [payment_data])
                    payment.write({'payment_posted_to_remote': True, 'remote_id': new_payment_id})
                    if payment.move_id:
                        payment.move_id.write({'posted_to_remote': True})  
                    # models.execute_kw(db, uid, password, 'account.payment', 'action_post', [[new_payment_id]])
                    # _logger.info("Payment Has been created *********************: %s", new_payment_id)
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
            'name': self.name,
            'partner_id': partner_id,
            'journal_id': self._map_journal_to_remote_company(models, db, uid, password, self.journal_id),
            'currency_id': currency_id  or None,
            'amount': self.amount or None,
            'date': self.date  or None,
            'payment_type': self.payment_type  or None,
            'partner_type': self.partner_type  or None,
            'memo': self.ref or None,
            'state': self.state,
            'company_id': self._map_branch_to_remote_company(models, db, uid, password, self.branch_id, self.company_id) 
            or self._map_remote_company(models, db, uid, password, self.company_id),
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
    
    def _map_remote_company(self, models, db, uid, password, company_id=None):
        """
        Map the branch or company to a remote company.

        If branch_id is not provided or is already a company object, fall back to using company_id.
        """
        remote_company_id = None
        local_company = None

        if company_id:
            # Fallback to using company_id if branch_id is not provided
            local_company = company_id
        else:
            raise ValueError("company_id must be provided to map to a remote company.")

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


