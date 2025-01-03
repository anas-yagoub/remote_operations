# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import requests, json, base64
from datetime import datetime, date
from odoo.exceptions import ValidationError, UserError
import xmlrpc.client
from pytz import timezone
import logging
_logger = logging.getLogger(__name__)



class AccountMove(models.Model):
    
    _inherit = 'account.move'
    
    posted_to_remote = fields.Boolean("Posted to remote")
    failed_to_sync = fields.Boolean("Failed to Sync", default=False)


    @api.model
    def action_send_account_moves_to_remote_cron(self):
        # Find all account.move records that are not posted to remote
        records_to_send = self.search([('posted_to_remote', '=', False),('state','=','posted'),('move_type', '=', 'entry'),('failed_to_sync', '=', False),('journal_id.type', '=', 'general')], limit=10)
       
        for rec in records_to_send:
            try:
                rec.send_account_moves_to_remote()
                # Commit the transaction after successfully processing the record
                self.env.cr.commit()
            except Exception as e:
                # Log the error
                _logger.error("Error processing record ID %s: %s", rec.id, str(e))
                # Rollback any partial changes and mark the record as failed
                self.env.cr.rollback()
                rec.write({'failed_to_sync': True})  # Mark record as failed to prevent retries
       

    def send_account_moves_to_remote(self):
        # Get configuration parameters
        config_parameters = self.env['ir.config_parameter'].sudo()

        remote_type = config_parameters.get_param('remote_operations.remote_type')
        if remote_type != 'Branch Database':
            _logger.info("Database is not configured as 'Branch Database'. Skipping sending account moves to remote.")
            return
        
        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        # Validate settings
        if not all([url, db, username, password]):
            raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

        # Create XML-RPC connection and send data
        try:
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url), allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url), allow_none=True)

            start_date = date(2024,7,1).strftime('%d/%m/%Y')
            # account_moves = self.search([('posted_to_remote', '=', False),('move_type', '=', 'entry')], limit=10)
            account_moves = self.sudo().search([('posted_to_remote', '=', False), \
                                                ('state', '=', 'posted'), ('move_type', '=', 'entry'), ('journal_id.type', '=', 'general') ,('failed_to_sync', '=', False),('date', '>=', start_date)], limit=10,
                                               order='date asc')
            # Get related account.move records
            # account_moves = self._get_related_account_moves()
            # account_moves = self.env['account.move'].search([])
            # print("**************************************")
            # account_moves = self.search(
            #     [('posted_to_remote', '=', False), ('state', '=', 'posted'), ('date', '>=', start_date)], limit=10)
            # print('*********************************', account_moves)
            # print('*********************************', account_moves.line_ids.read([]))

            for p in account_moves.line_ids:
                print(f"******************************** {p.read(['partner_id', 'account_id', 'debit'])}")

            for move in account_moves:    
                if move.journal_id.dont_synchronize:
                    continue
                # Ensure partner exists in remote database
                for line in move.line_ids:
                    print("*************************** Partner Name", line.partner_id.name)
                    if line.partner_id:
                        remote_partner_id = self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name',
                                                                       line.partner_id)
                        print("*************************** remote_partner_id Name", remote_partner_id)
                        if not remote_partner_id and line.partner_id:
                            # Create partner in remote database
                            remote_partner_id = self._create_remote_partner(models, db, uid, password, line.partner_id)
                            print("*************************** remote_partner_id Name (NEW)", remote_partner_id)

                company_id = self._get_remote_id(models, db, uid, password, 'res.company', 'name',
                                                 move.journal_id.company_id.name)
                move_data = self._prepare_move_data(models, db, uid, password, move, move.company_id.id)
                _logger.info("Account Move Data: %s", str(move_data))
                new_move = models.execute_kw(db, uid, password, 'account.move', 'create', [move_data])
                _logger.info("New Account Move: %s", str(new_move))
                move.write({'posted_to_remote': True})
                # Post the new move
                models.execute_kw(db, uid, password, 'account.move', 'action_post', [[new_move]])
                _logger.info("Posted Account Move: %s", str(new_move))
                self.write({'posted_to_remote': True})

        except Exception as e:
            raise ValidationError("Error while sending account move data to remote server: {}".format(e))

    def _prepare_move_data(self, models, db, uid, password, move, company_id):
        move_lines = []
        for line in move.line_ids:
            # account_to_check = line.account_id.code
            account_to_check = line.account_id.name
            if line.account_id.substitute_account:
                # account_to_check = line.account_id.substitute_account.code
                account_to_check = line.account_id.substitute_account.name

            print("Original Account: ", account_to_check, line.account_id.name,line.account_id.company_id.name)
            
            # account_id = self._map_account_to_remote_company(models, db, uid, password, company_id, account_to_check)
            account_id = self._map_account_name_to_remote_company(models, db, uid, password, company_id, account_to_check)

            print("account_idaccount_idaccount_idaccount_id", account_id)

            # account_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_to_check)
            currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', line.currency_id)
            # print("currency_idcurrency_idcurrency_id", currency_id)
            # Prepare the analytic distribution
            # analytic_distribution = self._prepare_analytic_distribution(models, db, uid, password, line.analytic_account_id)
            remote_analytic_account_id = self._prepare_analytic_distribution(models, db, uid, password, line.analytic_account_id)

            move_line_data = {
                'account_id': account_id,
                'name': line.name,
                'debit': line.debit,
                'credit': line.credit,
                'partner_id': self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name', line.partner_id) or None,
                'currency_id': currency_id,
                'amount_currency': line.amount_currency,
                'analytic_distribution': {str(remote_analytic_account_id): 100} if remote_analytic_account_id else {} or None,
            }

            # if move.move_type == ('out_invoice', 'in_invoice', 'in_receipt', 'out_receipt') and line.date_maturity:
            #     move_line_data['date_maturity'] = line.date_maturity

            move_lines.append((0, 0, move_line_data))
        move_data = {
            'patient': move.patient_id.name or None,
            'company_id': self._map_branch_to_remote_company(models, db, uid, password, move.branch_id, move.company_id) or None,
            'ref': move.ref or None,
            'date': move.date or None,
            'move_type': move.move_type or None,
            'currency_id': currency_id or None,
            # 'journal_id': self._get_remote_id(models, db, uid, password, 'account.journal', 'name', move.journal_id.name),
            'journal_id': self._map_journal_to_remote_company(models, db, uid, password, move.journal_id) or None,
        }

        if move.move_type in ('out_invoice', 'in_invoice', 'in_receipt', 'out_receipt'):
            inv_data = self._prepare_invoice_data(models, db, uid, password, move.company_id.id, move)
            partner_found = self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name',
                                                                       move.partner_id)
            if not partner_found:
                remote_partner_id = self._create_remote_partner(models, db, uid,password, move.partner_id)
                move_data['partner_id'] = remote_partner_id
            else:
                move_data['partner_id'] = partner_found

            move_data['invoice_line_ids'] = inv_data
            move_data['invoice_date_due'] = move.invoice_date_due or None
            move_data['invoice_date'] = move.invoice_date or None
            move_data['invoice_origin'] = move.invoice_origin or None
            move_data['payment_reference'] = move.payment_reference or None
            move_data['currency_id'] = currency_id or None
            move_data['narration'] = move.narration if move.narration else False

        else:

            move_data['line_ids'] = move_lines

        return move_data

    # def _prepare_invoice_data(self, models, db, uid, password,company_id, move):
    #     inv_move_lines = []
    #     for line in move.invoice_line_ids:
    #         account_to_check = line.account_id.name
    #         # account_to_check = line.account_id.code
    #         if line.account_id.substitute_account:
    #             account_to_check = line.account_id.substitute_account.name
    #
    #         # branch_company_id = self._map_branch_to_remote_company(models, db, uid, password, move.branch_id)
    #         # parent_company_id = self._get_remote_parent_company_id(models, db, uid, password, branch_company_id)
    #
    #         # account_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_to_check)
    #         account_id = self._map_account_name_to_remote_company(models, db, uid, password, move.company_id.id, account_to_check)
    #         currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name',
    #                                                  line.currency_id)
    #         # Prepare the analytic distribution
    #         remote_analytic_account_id = self._prepare_analytic_distribution(models, db, uid, password,
    #                                                                          line.analytic_account_id)
    #         tax_ids = [self._get_remote_id('account.tax', 'name', tax) for tax in line['tax_ids']]
    #
    #         inv_line_data = {
    #             'account_id': account_id,
    #             'name': line.name or None,
    #             'quantity': line.quantity or None,
    #             'price_unit': line.price_unit or None,
    #             'tax_ids': [(6, 0, tax_ids)] if tax_ids else [],
    #             'analytic_distribution': {str(remote_analytic_account_id): 100} if remote_analytic_account_id else {},
    #         }
    #         inv_move_lines.append((0, 0, inv_line_data))
    #
    #         print("""..........................................INV LINE DATA........................""")
    #         print(f"..........................................{inv_line_data}..............................")
    #         print(".......................................................................................")
    #
    #     return inv_move_lines

    def _prepare_analytic_distribution(self, models, db, uid, password, local_analytic_account_id):
        remote_analytic_account_id = None
        
        if local_analytic_account_id:
            local_analytic_account = self.env['account.analytic.account'].browse(int(local_analytic_account_id.id))            
            remote_analytic_account_id = self._get_remote_id(
                models, db, uid, password,
                'account.analytic.account', 'name',
                local_analytic_account.name
            )
        
        return remote_analytic_account_id
    
    
    def _map_branch_to_remote_company(self, models, db, uid, password, branch_id=None, company_id=None):
        """
        Map the branch or company to a remote company.

        If branch_id is not provided or is already a company object, fall back to using company_id.
        """
        remote_company_id = None
        local_company = None

        if branch_id:
            # Check if branch_id is a res.branch or res.company object
            # if hasattr(branch_id, 'company_id'):
            #     # branch_id is a res.branch object
            #     local_company = branch_id.company_id
            #     # print("*****************local_company from branch", local_company.name)
            # else:
                # branch_id is already a res.company object
            local_company = branch_id
                # print("*****************local_company directly from branch as company", local_company.name)
        elif company_id:
            # Fallback to using company_id if branch_id is not provided
            local_company = company_id
            # print("*****************local_company from company_id", local_company.name)
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
            print(f"domain>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>{domain}")
            remote_model = models.execute_kw(db, uid, password, model_name, 'search', [domain])
            print(f"remote_model[0]>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>{remote_model}")
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
    
    # def _map_account_to_remote_company(self, models, db, uid, password, account_to_check):
    #     if not account_to_check:
    #         return None

    #     # Get the local company linked to the journal
    #     local_company_id = account_to_check.company_id.id

    #     # Map to the remote company journal by name and company
    #     remote_account_id = self._get_remote_account_id(
    #         models, db, uid, password,
    #         'account.account',
    #         domain=[
    #             ('code', '=', account_to_check.code),  # Correct field access
    #             ('company_id', '=', local_company_id)
    #         ]
    #     )
    #     return remote_account_id

    # def _map_account_to_remote_company(self, models, db, uid, password, account_to_check, company_id):
    #     remote_account_id = None
    #     if account_to_check:
    #         # Map to the remote company account by code and company (by parent company as example)
    #         remote_account_id = self._get_remote_journal_id(
    #             models, db, uid, password,
    #             'account.account',
    #             domain=[
    #                 ('code', '=', account_to_check.code),
    #                 ('company_ids', 'in', [company_id])
    #             ]
                
    #         )
    #         print(f"PASSS>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>{remote_account_id}")

    #     return remote_account_id
    
    def _map_account_to_remote_company(self, models, db, uid, password, company_id, account_code):
        """
        Maps the account code to the remote company's account.
        """
        if not account_code:
            raise ValueError("Account code is required to map the remote account.")

        # Fetch the account ID in the remote database using the account code and company_id
        remote_account_id = self._get_remote_journal_id(
            models, db, uid, password,
            'account.account',
            domain=[
                ('code', '=', account_code),  # Match by account code
                ('company_ids', 'in', [company_id])  # Match by company ID
            ],
        )

        if not remote_account_id:
            raise ValidationError(f"No account found for code {account_code} in the remote company {company_id}.")

        print(f"Mapped Account Code {account_code} to Remote Account ID {remote_account_id}")
        return remote_account_id

    def _map_account_name_to_remote_company(self, models, db, uid, password, company_id, account_codename):
        """
        Maps the account name to the remote company's account.
        """
        print("============== Account name to search", account_codename)
        if not account_codename:
            raise ValueError("Account name is required to map the remote account.")

        # Fetch the account ID in the remote database using the account code and company_id
        remote_account_id = self._get_remote_journal_id(
            models, db, uid, password,
            'account.account',
            domain=[
                ('name', '=', account_codename),  # Match by account code
                ('company_ids', 'in', [company_id])  # Match by company ID
            ],
        )

        if not remote_account_id:
            raise ValidationError(f"No account found for name {account_codename} in the remote company {company_id}.")

        print(f"Mapped Account Name {account_codename} to Remote Account ID {remote_account_id}")
        return remote_account_id

    # def _prepare_analytic_distribution(self, models, db, uid, password, local_analytic_distribution):
    #     remote_analytic_distribution = {}
        
    #     if local_analytic_distribution:
    #         for local_analytic_account_id, distribution_percentage in local_analytic_distribution.items():
    #             local_analytic_account = self.env['account.analytic.account'].browse(int(local_analytic_account_id))
    #             remote_analytic_account_id = self._get_remote_id(models, db, uid, password, 'account.analytic.account', 'name', local_analytic_account.name)
    #             remote_analytic_distribution[str(remote_analytic_account_id)] = distribution_percentage

    #     return remote_analytic_distribution
    
    def _get_remote_company_id(self, models, db, uid, password):
        # Fetch the first company from the remote database
        remote_company = models.execute_kw(db, uid, password, 'res.company', 'search_read', [[]], {'fields': ['id'], 'limit': 1})
        if not remote_company:
            raise ValidationError(_("No company found in the remote database."))
        return remote_company[0]['id']

    # def _get_remote_parent_company_id(self, models, db, uid, password, company_id):
    #     # Fetch the first company from the remote database
    #     remote_company = models.execute_kw(db, uid, password, 'res.company', 'search_read', [[('id','=', company_id)]],
    #                                        {'fields': ['parent_id'], 'limit': 1})
    #     if not remote_company:
    #         raise ValidationError(_("No parent company found in the remote database."))

    #     parent_company_id = remote_company[0]['parent_id']
    #     print(f"Remote Parent Company ****************************{remote_company}********************************")

    #     return parent_company_id[0]

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


    # # def _get_remote_id(self, models, db, uid, password, model, field_name, field_value):
    # #     remote_record = models.execute_kw(db, uid, password, model, 'search_read', [[(field_name, '=', field_value)]], {'fields': ['id'], 'limit': 1})
    # #     if not remote_record:
    # #         raise ValidationError(_("The record for model '%s' with %s '%s' cannot be found in the remote database.") % (model, field_name, field_value))
    # #     return remote_record[0]['id']

    # def _get_remote_id_if_set(self, models, db, uid, password, model, field_name, field):
    #     if field:
    #         return self._get_remote_id(models, db, uid, password, model, field_name, field.name)
    #     return False
    
    # def _get_remote_id(self, models, db, uid, password, model, field_name, field_value):
    #     remote_record = models.execute_kw(db, uid, password, model, 'search_read', [[(field_name, '=', field_value)]], {'fields': ['id'], 'limit': 1})
    #     if not remote_record:
    #         raise ValidationError(_("The record for model '%s' with %s '%s' cannot be found in the remote database.") % (model, field_name, field_value))
    #     return remote_record[0]['id']

    def _get_remote_id_if_set(self, models, db, uid, password, model, field_name, field):
        if field:
            return self._get_remote_id(models, db, uid, password, model, field_name, field.name)
        return False

    
    def _create_remote_partner(self, models, db, uid, password, partner):
        """Create a partner in the remote database and return the new remote ID."""
        for rec in self:
            for line in rec.line_ids:
                account_receivable_id_to_check = line.partner_id.property_account_receivable_id.code
                account_payable_to_check = line.partner_id.property_account_payable_id.code

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

    
    @api.model
    def action_send_invoice_to_remote_cron(self):
        # Find all account.move records that are not posted to remote
        records_to_send = self.search(
            [('posted_to_remote', '=', False), ('state', '=', 'posted'), ('move_type', '!=', 'entry'),
             ('failed_to_sync', '=', False)], limit=10)
       
        for rec in records_to_send:
            try:
                print("\n\n\nPreparing to create invoice ----------\n\n\n")
                rec.send_invoice_to_remote()
                # Commit the transaction after successfully processing the record
                self.env.cr.commit()
            except Exception as e:
                # Log the error
                _logger.error("Error processing record ID %s: %s", rec.id, str(e))
                # Rollback any partial changes and mark the record as failed
                self.env.cr.rollback()
                rec.write({'failed_to_sync': True})  # Mark record as failed to prevent retries
    
    
    def send_invoice_to_remote(self):
        # Get configuration parameters
        config_parameters = self.env['ir.config_parameter'].sudo()

        remote_type = config_parameters.get_param('remote_operations.remote_type')
        if remote_type != 'Branch Database':
            _logger.info("Database is not configured as 'Branch Database'. Skipping sending account moves to remote.")
            return
        
        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        # Validate settings
        if not all([url, db, username, password]):
            raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

        # Create XML-RPC connection and send data
        try:
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url), allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url), allow_none=True)

            start_date = date(2024,7,1).strftime('%d/%m/%Y')
            # account_moves = self.search([('posted_to_remote', '=', False),('move_type', '=', 'entry')], limit=10)
            account_moves = self.sudo().search([('posted_to_remote', '=', False), \
                                                ('state', '=', 'posted'), ('move_type', '!=', 'entry') ,('failed_to_sync', '=', False),('date', '>=', start_date)], limit=10,
                                               order='date asc')
            # account_moves = self.sudo().search([('posted_to_remote', '=', False), \
            #                                     ('state', '=', 'posted'), ('move_type', '!=', 'entry') ,('failed_to_sync', '=', False)], limit=10,
            #                                   )
        

            for move in account_moves:    
                if move.journal_id.dont_synchronize:
                    continue
                # Ensure partner exists in remote database
                if move.partner_id:
                    remote_partner_id = self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name',
                                                                    move.partner_id)
                    print("*************************** remote_partner_id Name", remote_partner_id)
                    if not remote_partner_id and move.partner_id:
                        # Create partner in remote database
                        remote_partner_id = self._create_remote_partner(models, db, uid, password, move.partner_id)
                        print("*************************** remote_partner_id Name (NEW)", remote_partner_id)

                company_id = self._get_remote_id(models, db, uid, password, 'res.company', 'name',
                                                 move.journal_id.company_id.name)
                move_data = self._prepare_invoice_data(models, db, uid, password, move, move.company_id.id)
                _logger.info("Account Move Invoice Data: %s", str(move_data))
                new_move = models.execute_kw(db, uid, password, 'account.move', 'create', [move_data])
                _logger.info("New Account Move INV: %s", str(new_move))
                move.write({'posted_to_remote': True})
                # Post the new move
                models.execute_kw(db, uid, password, 'account.move', 'action_post', [[new_move]])
                _logger.info("Posted Account Move: %s", str(new_move))
                move.write({'posted_to_remote': True})
                
                # Now reconcile payments with the new remote invoice
                # payments = self.env['account.payment'].search([])  # Fetch payments related to the invoice

                # if payments:
                #     for payment in payments:
                        
                #         # Fetch payment lines from the payment
                #         for rec1 in payment.move_id.line_ids.filtered(lambda x: x.account_internal_type in ('asset_receivable', 'liability_payable') and not x.reconciled):
                #             # Reconcile the payment line with the invoice remotely
                            
                #             reconciled_result = models.execute_kw(db, uid, password, 'account.move', 'js_assign_outstanding_line', [rec1.id])
                #             _logger.info("Reconciled payment line %s with invoice %s", rec1.id, move.id)
               
                
                # payments = self.env['account.payment'].search([])
                # print("Payment******************************88", payments)

                # if payments:
                #     for payment in payments:
                #         # Loop through the payment lines of the payment and reconcile
                #         for rec1 in payment.move_id.line_ids.filtered(lambda x: x.account_type in ('asset_receivable', 'liability_payable') and not x.reconciled):
                #             # Ensure you're passing the ID or another valid attribute, not the entire record
                #             reconciled_result = move.sudo().js_assign_outstanding_line(rec1.id)
                #             _logger.info("Reconciled payment line %s with invoice %s", rec1.id, move.id)

        except Exception as e:
            raise ValidationError("Error while sending account move data to remote server: {}".format(e))    
    
    
    
    def _get_remote_tax_id(self, models, db, uid, password, model, field_name, field_value, company_id):
        """
        Fetches the remote tax ID based on the field value and company ID.
        """
        domain = [
            (field_name, '=', field_value), 
            '|',  
            ('company_id', '=', company_id),
            ('company_id', '=', False), 
            ('active', '=', True)
        ]

        _logger.info("Fetching remote tax ID with domain: %s", domain)

        remote_record = models.execute_kw(
            db, uid, password, model, 'search_read', 
            [domain], 
            {'fields': ['id', field_name, 'company_id'], 'limit': 1}
        )
        _logger.info("Remote record result: %s", remote_record)

        if not remote_record:
            _logger.warning(
                "No tax found for model '%s', field '%s' = '%s', company_id '%s'.",
                model, field_name, field_value, company_id
            )
            return None 

        return remote_record[0]['id']
            

    
    def _prepare_invoice_data(self, models, db, uid, password, move, company_id):
        move_lines = []
        for line in move.invoice_line_ids:
            account_to_check = line.account_id.name
            # account_to_check = line.account_id.code
            if line.account_id.substitute_account:
            #     account_to_check = line.account_id.substitute_account.code
                account_to_check = line.account_id.substitute_account.name

            account_id = self._map_account_name_to_remote_company(models, db, uid, password, company_id, account_to_check)
            currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', move.currency_id)
            remote_analytic_account_id = self._prepare_analytic_distribution(models, db, uid, password, line.analytic_account_id)
            tax_ids = [
                self._get_remote_tax_id(
                    models, db, uid, password,
                    'account.tax', 'name', tax.name, move.company_id.id
                )
                for tax in line.tax_ids
            ]

            move_line_data = {
                'account_id': account_id,
                'name': line.name,
                'analytic_distribution': {str(remote_analytic_account_id): 100} if remote_analytic_account_id else {} or None,
                'quantity': line.quantity or None,
                'price_unit': line.price_unit or None,
                'tax_ids': [(6, 0, tax_ids)] or None,

            }
            move_lines.append((0, 0, move_line_data))

        move_data = {
            'partner_id': self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name', move.partner_id) or None,
            'patient': move.patient_id.name or None,
            'company_id': self._map_branch_to_remote_company(models, db, uid, password, move.branch_id, move.company_id) or None,
            'payment_reference': move.payment_reference or None,
            'invoice_date': move.invoice_date or None,
            'invoice_date_due': move.invoice_date_due or None,
            'invoice_origin': move.invoice_origin or None,
            'narration': move.narration or None,
            'move_type': move.move_type or None,
            'currency_id': currency_id or None,
            'journal_id': self._map_journal_to_remote_company(models, db, uid, password, move.journal_id) or None,
            'invoice_line_ids': move_lines,
        }
        print(f"INV DATA MADE:-------------------\n\n{move_data}----------------\n\n")
        return move_data

    # def _map_account_invoice_to_remote_company(self, models, db, uid, password, move, account_code):
    #     """
    #     Maps the account code to the remote company's account.
    #     """
    #     if not account_code:
    #         raise ValueError("Account code is required to map the remote account.")
    #
    #     # Fetch the account ID in the remote database using the account code and company_id
    #     remote_account_id = self._get_remote_journal_id(
    #         models, db, uid, password,
    #         'account.account',
    #         domain=[
    #             ('name', '=', account_code),  # Match by account code
    #             ('company_ids', 'in', [move.company_id.id])  # Match by company ID
    #         ],
    #     )
    #
    #     if not remote_account_id:
    #         raise ValidationError(f"\n\n\nNo account found for code {account_code} in the remote company {move.company_id.name}.")
    #
    #     print(f"Mapped Account Code {account_code} to Remote Account ID {remote_account_id}")
    #     return remote_account_id
    
    