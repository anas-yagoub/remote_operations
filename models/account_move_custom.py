# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import requests, json, base64
from datetime import datetime, date
from odoo.exceptions import ValidationError, UserError
import xmlrpc.client
from pytz import timezone
import logging
from symbol import lambdef

_logger = logging.getLogger(__name__)



class AccountMove(models.Model):
    
    _inherit = 'account.move'
    
    posted_to_remote = fields.Boolean("Posted to remote", copy=False)
    failed_to_sync = fields.Boolean("Failed to Sync", default=False)
    remote_move_id = fields.Integer(string="Remote Move", copy=False)
    no_allow_sync = fields.Boolean("Not Allow Sync")

    
    def action_sync_to_remote_manual(self):
        """ Manually sync selected account moves to a remote Odoo server """
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

            for move in self:
                try:
                    if move.posted_to_remote or move.failed_to_sync:
                        continue  # Skip already processed records

                    if move.journal_id.dont_synchronize:
                        continue  # Skip journals marked not to sync

                    if move.date < start_date:
                        continue  # Skip moves before the sync date

                    # Ensure partners exist remotely
                    for line in move.line_ids:
                        if line.partner_id:
                            remote_partner_id = move._get_remote_id_if_set(
                                models, db, uid, password, 'res.partner', 'name', line.partner_id
                            )
                            if not remote_partner_id:
                                remote_partner_id = move._create_remote_partner(
                                    models, db, uid, password, line.partner_id
                                )

                    move_data = move._prepare_move_data(models, db, uid, password, move, move.company_id.id)
                    new_move_id = models.execute_kw(db, uid, password, 'account.move', 'create', [move_data])

                    models.execute_kw(db, uid, password, 'account.move', 'action_post', [[new_move_id]])

                    move.write({
                        'posted_to_remote': True,
                        'remote_move_id': new_move_id,
                        'failed_to_sync': False
                    })
                    self.env.cr.commit()

                    _logger.info(f"Successfully synced Move ID {move.id} to remote server with ID {new_move_id}")

                except Exception as move_error:
                    move.write({'failed_to_sync': True})
                    move.message_post(body=f"Error syncing Move ID {move.id}: {str(move_error)}")
                    _logger.error(f"Error syncing Move ID {move.id}: {str(move_error)}")
                    self.env.cr.rollback()

        except Exception as e:
            _logger.error(f"Critical error in remote sync: {str(e)}")
            raise UserError(_("Error while sending account move data to remote server: ") + str(e))


       
    
    
    def _prepare_analytic_distribution(self, models, db, uid, password, local_analytic_account, company_id=1):
        remote_analytic_account_id = None

        if local_analytic_account:
            domain = [
                ('name', '=', local_analytic_account.name),
                '|',
                ('company_id', '=', company_id),
                ('company_id', '=', False),
                ('active', '=', True)
            ]

            # Map the local analytic account to the remote analytic account by name and company
            remote_analytic_account_id = models.execute_kw(
                db, uid, password, 'account.analytic.account', 'search',
                [domain])[0]

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
            print(f"Journal domain>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>{domain}")
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
    
    
    
    def _get_remote_account_id(self, models, db, uid, password, model_name, domain=None):
        # If a domain is provided, use it to search
        if domain:
            print(f"Account domain>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>{domain}")
            remote_model = models.execute_kw(db, uid, password, model_name, 'search', [domain])
            print(f"remote_model[0]>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>{remote_model}")
        else:
            raise ValueError("Domain is required to search for remote records.")
    
    def _map_account_to_remote_company(self, models, db, uid, password, company_id, account_code):
        """
        Maps the account code to the remote company's account.
        """
        if not account_code:
            raise ValueError("Account code is required to map the remote account.")

        # Fetch the account ID in the remote database using the account code and company_id
        remote_account_id = self._get_remote_account_id(
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
        _logger.info(f"Account name to search {account_codename}")
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

    
    def _get_remote_company_id(self, models, db, uid, password):
        # Fetch the first company from the remote database
        remote_company = models.execute_kw(db, uid, password, 'res.company', 'search_read', [[]], {'fields': ['id'], 'limit': 1})
        if not remote_company:
            raise ValidationError(_("No company found in the remote database."))
        return remote_company[0]['id']

   

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


    def _get_remote_id_if_set(self, models, db, uid, password, model, field_name, field):
        if field:
            return self._get_remote_id(models, db, uid, password, model, field_name, field.name)
        return False

    def _create_remote_partner(self, models, db, uid, password, partner):
        """Create a partner in the remote database and return the new remote ID."""
        if partner:
            account_receivable_id_to_check = partner.property_account_receivable_id.code
            account_payable_to_check = partner.property_account_payable_id.code

            property_account_receivable_id = self._get_remote_id(models, db, uid, password, 'account.account',
                                                                 'code', account_receivable_id_to_check)
            property_account_payable_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code',
                                                              account_payable_to_check)
            partner_data = {
                'name': partner.name,
                'email': partner.email,
                'phone': partner.phone,
                'is_company': partner.is_company,
                'company_type': partner.company_type,
                'mobile': partner.mobile,
                'street': partner.street,
                'street2': partner.street2,
                'city': partner.city,
                'zip': partner.zip,
                'country_id': self._get_remote_id_if_set(models, db, uid, password, 'res.country', 'name',
                                                         partner.country_id) or False,
                # 'state_id': self._get_remote_id_if_set(models, db, uid, password, 'res.country.state', 'name', partner.state_id),
                'vat': partner.vat,
                'customer_rank': partner.customer_rank,
                'supplier_rank': partner.supplier_rank,
                'property_account_receivable_id': property_account_receivable_id,
                'property_account_payable_id': property_account_payable_id,
            }
            _logger.info(f"\n\n\n---------------------------------------------------\nPARTNER {partner_data} \n\n\n")
            return models.execute_kw(db, uid, password, 'res.partner', 'create', [partner_data])
            # return partner_data

    # @api.model
    # def action_send_invoice_to_remote_cron(self):
    #     # Get configuration parameters
    #     config_parameters = self.env['ir.config_parameter'].sudo()
        
    #     remote_type = config_parameters.get_param('remote_operations.remote_type')
    #     if remote_type != 'Branch Database':
    #         _logger.info("Database is not configured as 'Branch Database'. Skipping sending account moves to remote.")
    #         return
        
    #     url = config_parameters.get_param('remote_operations.url')
    #     db = config_parameters.get_param('remote_operations.db')
    #     username = config_parameters.get_param('remote_operations.username')
    #     password = config_parameters.get_param('remote_operations.password')
        
    #     # Validate settings
    #     if not all([url, db, username, password]):
    #         raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")
        
    #     try:
    #         # Create XML-RPC connection
    #         common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url), allow_none=True)
    #         uid = common.authenticate(db, username, password, {})
    #         models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url), allow_none=True)
            
    #         start_date = date(2025, 5, 1).isoformat()
    #         account_moves = self.sudo().search([
    #             ('posted_to_remote', '=', False), 
    #             ('state', '=', 'posted'),
    #             ('move_type', '!=', 'entry'),
    #             ('failed_to_sync', '=', False),
    #             ('date', '>=', start_date),
    #             ('no_allow_sync','=', False)
    #         ], limit=5, order='date asc')
            
    #         _logger.info(f"Account Moves to Process: {account_moves.read(['name', 'posted_to_remote'])}")
            
    #         for move in account_moves:
    #             try:
    #                 # Skip moves linked to journals marked as "don't synchronize"
    #                 if move.journal_id.dont_synchronize:
    #                     continue
                    
    #                 # Prepare and send data to the remote server
    #                 company_id = self._get_remote_id(models, db, uid, password, 'res.company', 'name', move.journal_id.company_id.name)
    #                 _logger.info(f"Processing Move: {move.read(['name', 'company_id', 'partner_id'])}")
                    
    #                 move_data = self._prepare_invoice_data(models, db, uid, password, move, move.company_id.id)
    #                 _logger.info("Prepared Move Data: %s", str(move_data))
                    
    #                 new_move = models.execute_kw(db, uid, password, 'account.move.custom', 'create', [move_data])
    #                 _logger.info("Created Remote Move: %s", str(new_move))
    #                 move.write({'posted_to_remote': True, 'remote_move_id': new_move})
    #                 # Post the move remotely
    #                 # models.execute_kw(db, uid, password, 'account.move.custom', 'action_post', [[new_move]])
    #                 # _logger.info("Posted Remote Move: %s", str(new_move))
    #                 # Mark move as posted to remote
    #             except Exception as inner_e:
    #                 # Log and mark move as failed
    #                 move.write({'failed_to_sync': True})
    #                 _logger.error("Error Processing Move ID %s: %s", move.id, str(inner_e))
    #                 move.message_post(body="Error processing Move ID {}: {}".format(move.id, str(inner_e)))

            
    #         # Log summary of the process
    #         successful_moves = account_moves.filtered(lambda m: m.posted_to_remote)
    #         failed_moves = account_moves.filtered(lambda m: not m.posted_to_remote)
            
    #         _logger.info(f"Successfully Processed Moves: {len(successful_moves)}")
    #         _logger.info(f"Failed to Process Moves: {len(failed_moves)}")
        
    #     except Exception as outer_e:
    #         # Catch errors at the overall level
    #         _logger.error("Error While Sending Account Moves to Remote Server: %s", str(outer_e))
    #         raise ValidationError("Error while sending account move data to remote server: {}".format(outer_e))
    
    @api.model
    def action_send_invoice_to_remote_cron(self):
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
        
        try:
            # Create XML-RPC connection
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url), allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url), allow_none=True)
            
            start_date = date(2025, 5, 1).isoformat()
            account_moves = self.sudo().search([
                ('posted_to_remote', '=', False), 
                ('state', '=', 'posted'),
                ('move_type', '!=', 'entry'),
                ('failed_to_sync', '=', False),
                ('date', '>=', start_date),
                ('no_allow_sync','=', False)
            ], limit=5, order='date asc')
            
            _logger.info(f"Account Moves to Process: {account_moves.read(['name', 'posted_to_remote'])}")
            
            for move in account_moves:
                try:
                    # Skip moves linked to journals marked as "don't synchronize"
                    if move.journal_id.dont_synchronize:
                        continue
                    
                    # Check if move already exists in remote using remote_move_id
                    if move.remote_move_id:
                        existing_move_ids = models.execute_kw(db, uid, password, 'account.move.custom', 'search', [[('id', '=', move.remote_move_id)]])
                        if existing_move_ids:
                            _logger.info(f"Skipping Move ID {move.id}: Already exists in remote with ID {move.remote_move_id}")
                            continue
                    
                    # Prepare and send data to the remote server
                    company_id = self._get_remote_id(models, db, uid, password, 'res.company', 'name', move.journal_id.company_id.name)
                    _logger.info(f"Processing Move: {move.read(['name', 'company_id', 'partner_id'])}")
                    
                    move_data = self._prepare_invoice_data(models, db, uid, password, move, move.company_id.id)
                    _logger.info("Prepared Move Data: %s", str(move_data))
                    
                    # Create new move
                    new_move = models.execute_kw(db, uid, password, 'account.move.custom', 'create', [move_data])
                    _logger.info("Created Remote Move: %s", str(new_move))
                    move.write({'posted_to_remote': True, 'remote_move_id': new_move})
                    
                    # Post the move remotely
                    # models.execute_kw(db, uid, password, 'account.move.custom', 'action_post', [[new_move]])
                    # _logger.info("Posted Remote Move: %s", str(new_move))
                    
                except Exception as inner_e:
                    # Log and mark move as failed
                    move.write({'failed_to_sync': True})
                    _logger.error("Error Processing Move ID %s: %s", move.id, str(inner_e))
                    move.message_post(body="Error processing Move ID {}: {}".format(move.id, str(inner_e)))
            
            # Log summary of the process
            successful_moves = account_moves.filtered(lambda m: m.posted_to_remote)
            failed_moves = account_moves.filtered(lambda m: not m.posted_to_remote)
            
            _logger.info(f"Successfully Processed Moves: {len(successful_moves)}")
            _logger.info(f"Failed to Process Moves: {len(failed_moves)}")
        
        except Exception as outer_e:
            # Catch errors at the overall level
            _logger.error("Error While Sending Account Moves to Remote Server: %s", str(outer_e))
            raise ValidationError("Error while sending account move data to remote server: {}".format(outer_e))

   
   
    def _prepare_analytic_distribution(self, models, db, uid, password, local_analytic_account, company_id=1):
        remote_analytic_account_id = None

        if local_analytic_account:
            domain = [
                ('name', '=', local_analytic_account.name),
                '|',
                ('company_id', '=', company_id),
                ('company_id', '=', False),
                ('active', '=', True)
            ]

            # Map the local analytic account to the remote analytic account by name and company
            remote_analytic_account_id = models.execute_kw(
                db, uid, password, 'account.analytic.account', 'search',
                [domain])[0]

            return remote_analytic_account_id

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
        item_lines = []
        partner = False
        
        for line in move.line_ids:
            # if line.display_type in ('product', 'line_section', 'line_note'):
            #     continue
            # account_type = line.account_id.user_type_id.type
            # if account_type not in ('receivable', 'income'):
            #     continue  # Skip any account that is not receivable or income
            
            account_code = line.account_id.code
            account_name_to_check = line.account_id.name
      
            account_id = self._map_account_name_to_remote_company(models, db, uid, password, company_id,
                                                                      account_name_to_check)

            currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', line.currency_id)
            
            remote_analytic_account_id = self._prepare_analytic_distribution(models, db, uid, password, line.analytic_account_id,
                                                                             company_id)
            analytic_distributions = [
                    self._get_remote_tax_id(
                        models, db, uid, password,
                        'account.analytic.account', 'name', analytic.name, move.company_id.id
                    )
                    for analytic in line.analytic_account_id
                ]

            move_item_data = {
                'account_id': account_id,
                'name': line.name,
                'debit': line.debit,
                'credit': line.credit,
                'partner_id': self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name', line.partner_id) or None,
                'currency_id': currency_id,
                'amount_currency': line.amount_currency,
                'analytic_distribution': [(4, analytic) for analytic in analytic_distributions] if analytic_distributions else None,

                # 'analytic_distribution': {str(remote_analytic_account_id): 100} if remote_analytic_account_id else {} or None,
            }

            item_lines.append((0, 0, move_item_data))
        
        for line in move.invoice_line_ids:
            if line.display_type and line.display_type not in ('product', 'line_section', 'line_note'):
               continue
            # if line.display_type not in ('product', 'line_section', 'line_note'):
            #     continue
            
            account = line.account_id
            account_name_to_check = account.name
            if account.substitute_account:
                account_name_to_check = account.substitute_account.name
            if account_name_to_check:
                account_id = self._map_account_name_to_remote_company(models, db, uid, password, company_id,
                                                                      account_name_to_check)
                remote_analytic_account_id = self._prepare_analytic_distribution(models, db, uid, password,
                                                                                 line.analytic_account_id, company_id)
                tax_ids = [
                    self._get_remote_tax_id(
                        models, db, uid, password,
                        'account.tax', 'name', tax.name, move.company_id.id
                    )
                    for tax in line.tax_ids
                ]
                
                analytic_distributions = [
                    self._get_remote_tax_id(
                        models, db, uid, password,
                        'account.analytic.account', 'name', analytic.name, move.company_id.id
                    )
                    for analytic in line.analytic_account_id
                ]
            else:
                account_id = None
                remote_analytic_account_id = None
                tax_ids = []
                analytic_distributions = []
                
            product = self._get_remote_id_if_set(models, db, uid, password, 'product.product', 'name', line.product_id)

            move_line_data = {
                'product_id': product if product else False,  
                'name': line.name if not product else False,
                'account_id': account_id,
                # 'analytic_distribution': {
                #     str(remote_analytic_account_id): 100} if remote_analytic_account_id else {} or None,
                'quantity': line.quantity or None,
                'price_unit': line.price_unit or None,
                'tax_ids': [(4, tax) for tax in tax_ids] if tax_ids else None,
                'analytic_distribution': [(4, analytic) for analytic in analytic_distributions] if analytic_distributions else None,
                # 'display_type': line.display_type if line.display_type in ['line_section', 'line_note'] else 'product',
                'price_subtotal': line.price_subtotal,
                'product_uom_id': line.product_uom_id.id or None,
                
            }
            move_lines.append((0, 0, move_line_data))

        currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', move.currency_id)
        # # Ensure partner exists in remote database
        if move.partner_id:
            partner = self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name',
                                                 move.partner_id)

            if not partner and move.partner_id:
                # Create partner in remote database and update status
                remote_partner_id2 = self._create_remote_partner(models, db, uid, password, move.partner_id)
                if remote_partner_id2 or remote_partner_id2 != None:
                    move.partner_id.write({'sent_to_remote': True})
                _logger.info(f"*************************** remote_partner_id Name (NEW) {remote_partner_id2}")
                _logger.info(f"*************************** remote_partner_id Name (OLD) {partner}")

            move_data = {
                'payment_state': move.payment_state,
                'state': move.state,
                'name': move.name,
                'partner_id': self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name',
                                                 move.partner_id),
                'patient': move.patient_id.name or None,
                'company_id': self._map_branch_to_remote_company(models, db, uid, password, move.branch_id, move.company_id) or 
                self._map_remote_company(models, db, uid, password, move.company_id),
                'payment_reference': move.payment_reference or None,
                'ref': move.ref or None,
                'invoice_date': move.invoice_date or None,
                'date': move.date or None,
                'invoice_date_due': move.invoice_date_due or None,
                'invoice_origin': move.invoice_origin or None,
                'narration': move.narration or None,
                'move_type': move.move_type or None,
                'currency_id': currency_id or None,
                'journal_id': self._map_journal_to_remote_company(models, db, uid, password, move.journal_id) or None,
                'invoice_line_ids': move_lines,
                'line_ids': item_lines,
                # 'amount_paid': move.amount_paid,
                'amount_residual': move.amount_residual,
                'amount_tax': move.amount_tax,
                'amount_untaxed': move.amount_untaxed,
                'amount_total': move.amount_total,
                'source_state': move.state,
            }
            _logger.info(f"**************************************** Invoice line ids {move_lines}")

            _logger.info(f"%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%% line ids {item_lines}")

        return move_data
    
    
    # def button_cancel(self):
    #     result = super(AccountMove, self).button_cancel()
    #     for move in self:
    #         move._reset_cancel_remote_record()
    #     return result
    
    # def button_draft(self):
    #     result = super(AccountMove, self).button_draft()
    #     for move in self:
    #         move._reset_remote_record()
    #     return result
    
    # def action_post(self):
    #     super().action_post()
    #     for move in self:
    #         if move.move_type == 'entry':
    #             move._update_remote_record()
    #         else: 
    #             move._update_invoice_remote_record()
    
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
    
    def _reset_cancel_remote_record(self):
        """Reset the corresponding record in the remote Odoo 18 database."""
        self.ensure_one()
        if not self.remote_move_id:
            return  # No remote record to reset

        config_parameters = self.env['ir.config_parameter'].sudo()
        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        if not all([url, db, username, password]):
            _logger.error("Remote server settings are incomplete.")
            return

        try:
            # Connect to the remote Odoo database
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url), allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url), allow_none=True)

            # Reset the state of the remote record to draft
            _logger.info("Resetting remote record ID %s to cancel.", self.remote_move_id)
            models.execute_kw(
                db, uid, password, 
                'account.move', 
                'write', 
                [[self.remote_move_id], {'state': 'cancel'}]
            )
            _logger.info("Successfully reset remote record ID %s to cancel.", self.remote_move_id)

        except Exception as e:
            _logger.error("Error resetting remote record ID %s to cancel: %s", self.remote_move_id, str(e))
        


    def _reset_remote_record(self):
        """Reset the corresponding record in the remote Odoo 18 database."""
        self.ensure_one()
        if not self.remote_move_id:
            return  # No remote record to reset

        config_parameters = self.env['ir.config_parameter'].sudo()
        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        if not all([url, db, username, password]):
            _logger.error("Remote server settings are incomplete.")
            return

        try:
            # Connect to the remote Odoo database
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url), allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url), allow_none=True)

            # Reset the state of the remote record to draft
            _logger.info("Resetting remote record ID %s to draft.", self.remote_move_id)
            models.execute_kw(
                db, uid, password, 
                'account.move', 
                'write', 
                [[self.remote_move_id], {'state': 'draft'}]
            )
            _logger.info("Successfully reset remote record ID %s to draft.", self.remote_move_id)

        except Exception as e:
            _logger.error("Error resetting remote record ID %s to draft: %s", self.remote_move_id, str(e))
            
      
    
    def _update_remote_record(self):
        """Update the corresponding record in the remote Odoo 18 database."""
        self.ensure_one()
        if not self.remote_move_id:
            return  # No remote record to update

        # Fetch remote configuration parameters
        config_parameters = self.env['ir.config_parameter'].sudo()
        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        if not all([url, db, username, password]):
            _logger.error("Remote server settings are incomplete.")
            return

        try:
            # Connect to the remote Odoo database
            common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

            # Step 1: Delete existing line_ids in the remote record
            remote_line_ids = models.execute_kw(
                db, uid, password, 
                'account.move.line', 
                'search', 
                [[('move_id', '=', self.remote_move_id)]]
            )
            if remote_line_ids:
                models.execute_kw(
                    db, uid, password, 
                    'account.move.line', 
                    'unlink', 
                    [remote_line_ids]
                )
                _logger.info("Successfully deleted remote line_ids for remote record ID %s.", self.remote_move_id)

            update_data = self._prepare_remote_update_data(models, db, uid, password, self, self.company_id.id)
            _logger.info("Updating remote record ID %s with data: %s", self.remote_move_id, update_data)
            # Step 3: Update the record in the remote database
            models.execute_kw(
                db, uid, password, 
                'account.move', 
                'write', 
                [[self.remote_move_id], update_data]
            )
            _logger.info("Successfully updated remote record ID %s.", self.remote_move_id)
            
            # Post the updated move
            models.execute_kw(db, uid, password, 'account.move', 'action_post', [[self.remote_move_id]])

        except Exception as e:
            _logger.error("Error updating remote record ID %s: %s", self.remote_move_id, str(e))
            self.message_post(body="Error processing Move ID {}: {}".format( self.remote_move_id, str(e)))

            
            
    def _prepare_remote_update_data(self, models, db, uid, password, move, company_id):
        move_lines = []
        for line in move.line_ids:
            account_to_check = line.account_id.code
            if line.account_id.substitute_account:
                account_to_check = line.account_id.substitute_account.code
    
            account_id = self._map_account_to_remote_company(models, db, uid, password, company_id, account_to_check)

            currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', line.currency_id)
            
            remote_analytic_account_id = self._prepare_analytic_distribution(models, db, uid, password, \
                                                                             line.analytic_account_id, company_id)

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

            move_lines.append((0, 0, move_line_data))
            
        move_data = {
            'patient': move.patient_id.name or None,
            'company_id': self._map_branch_to_remote_company(models, db, uid, password, move.branch_id, move.company_id) or None,
            'ref': move.ref or None,
            'date': move.date or None,
            'move_type': move.move_type or None,
            'currency_id': currency_id or None,
            'journal_id': self._map_journal_to_remote_company(models, db, uid, password, move.journal_id) or None,
            'line_ids': move_lines,
        }
        
        return move_data
    
    


    def _update_invoice_remote_record(self):
        """Update the corresponding record in the remote Odoo 18 database."""
        self.ensure_one()
        if not self.remote_move_id:
            return  # No remote record to update

        # Fetch remote configuration parameters
        config_parameters = self.env['ir.config_parameter'].sudo()
        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        if not all([url, db, username, password]):
            _logger.error("Remote server settings are incomplete.")
            return

        try:
            # Connect to the remote Odoo database
            common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

            # Step 1: Fetch the account.move record to get invoice_line_ids
            remote_move = models.execute_kw(
                db, uid, password,
                'account.move',
                'search_read',
                [[('id', '=', self.remote_move_id)]],  # Searching for the specific move_id
                {'fields': ['invoice_line_ids']}  # Only fetch invoice_line_ids field
            )

            if remote_move:
                remote_invoice_line_ids = remote_move[0].get('invoice_line_ids', [])
                _logger.info("Fetched remote invoice_line_ids for remote record ID %s: %s", self.remote_move_id, remote_invoice_line_ids)

                # Step 2: If invoice_line_ids exist, unlink them
                if remote_invoice_line_ids:
                    models.execute_kw(
                        db, uid, password,
                        'account.move.line',
                        'unlink',
                        [remote_invoice_line_ids]  # Unlink the invoice lines
                    )
                    _logger.info("Successfully unlinked remote invoice_line_ids for remote record ID %s.", self.remote_move_id)

                # Step 3: Prepare the updated data for the invoice
                update_data = self._prepare_invoice_data(models, db, uid, password, self, self.company_id.id)
                _logger.info("Updating remote record ID %s with data: %s", self.remote_move_id, update_data)

                # Step 4: Update the record in the remote database
                models.execute_kw(
                    db, uid, password,
                    'account.move',
                    'write',
                    [[self.remote_move_id], update_data]
                )
                models.execute_kw(db, uid, password, 'account.move', 'action_post', [[self.remote_move_id]])

                _logger.info("Successfully updated remote record ID %s.", self.remote_move_id)

            else:
                _logger.error("No account.move found for remote record ID %s", self.remote_move_id)
                

        except Exception as e:
            _logger.error("Error updating remote record ID %s: %s", self.remote_move_id, str(e))
            self.message_post(body="Error processing Move ID {}: {}".format(self.remote_move_id, str(e)))


                
            




                    
                
