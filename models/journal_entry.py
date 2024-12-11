# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import requests, json, base64
from datetime import datetime, date
from odoo.exceptions import ValidationError, UserError
import xmlrpc.client
from pytz import timezone
import logging
_logger = logging.getLogger(__name__)

BATCH_SIZE = 100  # Number of records to process per batch


class AccountMove(models.Model):
    
    _inherit = 'account.move'
    
    posted_to_remote = fields.Boolean("Posted to remote")
    
    @api.model
    def action_send_account_moves_to_remote_cron(self):
        # Find all account.move records that are not posted to remote
        records_to_send = self.search([('posted_to_remote', '=', False)])
        for rec in records_to_send:
            print("Processing record: ", rec.id, "LABEL\n\n", rec.name)
            rec.send_account_moves_to_remote()
            rec.posted_to_remote = True
            print("Done processing record: ", "LABEL\n\n", rec.id)
    

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
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))

            # Get related account.move records
            # account_moves = self._get_related_account_moves()
            account_moves = self.env['account.move'].search([])
            for move in account_moves:
                if move.journal_id.dont_synchronize:
                    continue
                company_id = self._get_remote_id(models, db, uid, password, 'res.company', 'name',\
                                                 move.journal_id.company_id.name)
                move_data = self._prepare_move_data(models, db, uid, password, move, company_id)
                _logger.info("Account Move Data: %s ------------PASSS--------------------\n ", str(move_data))
                # new_move = models.execute_kw(db, uid, password, 'account.move', 'create', [move_data])
                # _logger.info("New Account Move: %s", str(new_move))
                #
                # # Post the new move
                # models.execute_kw(db, uid, password, 'account.move', 'action_post', [[new_move]])
                # _logger.info("Posted Account Move: %s", str(new_move))
                _logger.info("Account Move  (DRAFT) : %s ------------PASSS--------------------\n ", str(move_data))
                # move.write({'posted_to_remote': True})

        except Exception as e:
            raise ValidationError("Error while sending account move data to remote server: {}".format(e))

    def _prepare_move_data(self, models, db, uid, password, move, company_id):
        move_lines = []
        for line in move.line_ids:
            # Skip empty Account Codes.. Code - False
            account_to_check = line.account_id.code
            if line.account_id.substitute_account:
                account_to_check = line.account_id.substitute_account.code

            account_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_to_check)
            _logger.info("\n\n\n\Account Move Data\n\n\n\n\n\n: %s", str(account_id))

            currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', line.currency_id)
            _logger.info("\n\n\n\Account Move Currency Data\n\n\n\n\n\n: %s", str(currency_id))

             # Prepare the analytic distribution
            # analytic_distribution = self._prepare_analytic_distribution(models, db, uid, password, line.analytic_account_id)
            remote_analytic_account_id = self._prepare_analytic_distribution(models, db, uid, password, line.analytic_account_id)



            partner = self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name',
                                                 line.partner_id)
            if not partner and line.partner_id:
                self._set_missing_partner(partner=line.partner_id)

            partner = self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name',
                                                 line.partner_id)

            move_line_data = {
                'account_id': account_id,
                'name': line.name,
                'debit': line.debit,
                'credit': line.credit,
                'currency_id': currency_id,
                'partner_id': partner,
                'amount_currency': line.amount_currency,
                # 'analytic_distribution': analytic_distribution,
                'analytic_distribution': {str(remote_analytic_account_id): 100} if remote_analytic_account_id else {},
            }
            #
            move_lines.append((0, 0, move_line_data))
            branch_company_id = self._map_branch_to_remote_company(models, db, uid, password, move.branch_id)
            patient_char = move.patient_id.name if move.patient_id else None

            move_data = {
                'patient': patient_char,
                'company_id': branch_company_id,
                'ref': move.ref,
                'date': move.date,
                'move_type': move.move_type,
                'currency_id': currency_id,
                'journal_id': self._get_remote_id(models, db, uid, password, 'account.journal', 'name', move.journal_id.name),
                'line_ids': move_lines,
            }

        return move_data
    
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
    
    def _map_branch_to_remote_company(self, models, db, uid, password, branch_id):
        remote_company_id = None
        if branch_id:
            # Get the local company linked to the branch
            local_company = branch_id

            # Map to the remote company by name or another unique field
            remote_company_id = self._get_remote_id(
                models, db, uid, password,
                'res.company', 'name', local_company.name
            )
        return remote_company_id



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

    def _get_remote_id(self, models, db, uid, password, model, field_name, field_value):
        remote_record = models.execute_kw(db, uid, password, model, 'search_read', [[(field_name, '=', field_value)]], {'fields': ['id'], 'limit': 1})
        if not remote_record:
            raise ValidationError(_("The record for model '%s' with %s '%s' cannot be found in the remote database.") % (model, field_name, field_value))
        return remote_record[0]['id']

    def _get_remote_id_if_set(self, models, db, uid, password, model, field_name, field):
        if field:
            return self._get_remote_id(models, db, uid, password, model, field_name, field.name)
        return False

    def _set_missing_partner(self, partner):
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

        try:
            # Authenticate with remote server
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
            uid = common.authenticate(db, username, password, {})
            if not uid:
                raise ValidationError("Authentication failed for the remote server.")
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))

            partners = self.env['res.partner'].search([('active', '=', True)])

            # Get partner
            partner = partners.filtered(lambda p: p.name == partner.name)

            if partner.related_company:
                self._create_partner_invoice(db=db, uid=uid, username=username, password=password, partners_list=partners,
                                         partner=partner.related_company)
            else:
                self._create_partner_invoice(db=db, uid=uid, username=username, password=password, partners_list=partners,
                                             partner=partner)
        except Exception as e:
            return False

    def _create_partner_invoice(self, db, uid, password, username, partners_list, partner):
        for partner in partners_list:
            # Replace this with the actual logic for sending partner data to the remote system
            _logger.info(f"Processing partner ID: {partner.id}, Name: {partner.name}")

            # Check if partner exists on remote
            existing_partner_ids = models.execute_kw(
                db, uid, password, 'res.partner', 'search',
                [[('name', '=', partner.name), ('email', '=', partner.email)]]
            )

            if existing_partner_ids:
                _logger.info("Partner already exists on remote: %s", partner.name)
                continue

            # Prepare partner data for remote creation
            partner_data = {
                'name': partner.name,
                'email': partner.email,
                'is_company': partner.is_company,
                'phone': partner.phone,
                'mobile': partner.mobile,
                'street': partner.street,
                'street2': partner.street2,
                'city': partner.city,
                'state_id': self._get_remote_id(models, db, uid, password, 'res.country.state', 'name',
                                                partner.state_id.name) if partner.state_id else False,
                'country_id': self._get_remote_id(models, db, uid, password, 'res.country', 'name',
                                                  partner.country_id.name) if partner.country_id else False,
                'company_id': self._get_remote_id(models, db, uid, password, 'res.company', 'name',
                                                  partner.company_id.name) if partner.company_id else False,

                # Accounts
                'property_account_receivable_id': self._get_remote_id(models, db, uid, password,
                                                                      'account.account', \
                                                                      'code',
                                                                      partner.property_account_receivable_id.code),
                'property_account_payable_id': self._get_remote_id(models, db, uid, password, 'account.account', \
                                                                   'code',
                                                                   partner.property_account_payable_id.code),
            }

            # Create partner on remote
            remote_partner_id = models.execute_kw(db, uid, password, 'res.partner', 'create', [partner_data])
            _logger.info("Created Partner on remote: %s (ID: %s)", partner.name, remote_partner_id)
            print("Created Partner on remote: %s (ID: %s)", partner.name, remote_partner_id)
            print("(INV PARTNER) -------------------------\n\n\n----------------------------------------------------------------")
            # partner.write({'posted_to_remote': True, 'last_processed_partner_id': partner.id})


class ResPartner(models.Model):
    _inherit = 'res.partner'

    posted_to_remote = fields.Boolean(required=False)

    # Add a field to track processed partners
    last_processed_partner_id = fields.Integer(
        string="Last Processed Partner",
        help="Tracks the last processed partner for batch cron job execution."
    )

    @api.model
    def action_send_partner(self):
        """
        This method is triggered by the cron job to send partner data to a remote system.
        Customize the logic to suit your integration requirements.
        """
        # Get configuration parameters
        config_parameters = self.env['ir.config_parameter'].sudo()

        remote_type = config_parameters.get_param('remote_operations.remote_type')
        if remote_type != 'Branch Database':
            _logger.info(
                "Database is not configured as 'Branch Database'. Skipping sending partners to remote.")
            return

        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        # Validate settings
        if not all([url, db, username, password]):
            raise ValidationError(
                "Remote server settings must be fully configured (URL, DB, Username, Password)")
        try:
            # Example: Fetch all active partners
            # Get the last processed partner ID from ir.config_parameter
            # last_processed_id = int(
            #     self.env['ir.config_parameter'].sudo().get_param('res_partner_last_processed_id', default=0))

            # Fetch the next batch of partners
            partners = self.search(
                [('active', '=', True),('posted_to_remote', '=', False)],
            )

            if not partners:
                _logger.info("No more partners to process. Resetting the batch process.")
                # Reset last processed ID to start again if needed
                print("Already....")
                # self.env['ir.config_parameter'].sudo().set_param('res_partner_last_processed_id', 0)
                return

            # Authenticate with remote server
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
            uid = common.authenticate(db, username, password, {})
            if not uid:
                raise ValidationError("Authentication failed for the remote server.")
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))

            # Get companies
            companies = partners.filtered(lambda p: p.is_company)
            companies_filtered = companies
            if len(companies_filtered) > 0:
                for partner in companies_filtered:
                    # Replace this with the actual logic for sending partner data to the remote system
                    _logger.info(f"Processing partner ID: {partner.id}, Name: {partner.name}")

                    # Check if partner exists on remote
                    existing_partner_ids = models.execute_kw(
                        db, uid, password, 'res.partner', 'search',
                        [[('name', '=', partner.name), ('email', '=', partner.email)]]
                    )

                    if existing_partner_ids:
                        _logger.info("Partner already exists on remote: %s", partner.name)
                        continue

                    # Prepare partner data for remote creation
                    partner_data = {
                        'name': partner.name,
                        'email': partner.email,
                        'is_company': partner.is_company,
                        'phone': partner.phone,
                        'mobile': partner.mobile,
                        'street': partner.street,
                        'street2': partner.street2,
                        'city': partner.city,
                        'state_id': self._get_remote_id(models, db, uid, password, 'res.country.state', 'name',
                                                        partner.state_id.name) if partner.state_id else False,
                        'country_id': self._get_remote_id(models, db, uid, password, 'res.country', 'name',
                                                          partner.country_id.name) if partner.country_id else False,
                        'company_id': self._get_remote_id(models, db, uid, password, 'res.company', 'name',
                                                          partner.company_id.name) if partner.company_id else False,

                        # Accounts
                        'property_account_receivable_id': self._get_remote_id(models, db, uid, password,
                                                                              'account.account', \
                                                                              'code',
                                                                              partner.property_account_receivable_id.code),
                        'property_account_payable_id': self._get_remote_id(models, db, uid, password, 'account.account', \
                                                                           'code',
                                                                           partner.property_account_payable_id.code),
                    }

                    # Create partner on remote
                    remote_partner_id = models.execute_kw(db, uid, password, 'res.partner', 'create', [partner_data])
                    _logger.info("Created Partner on remote: %s (ID: %s)", partner.name, remote_partner_id)
                    print("DSDSDSDSD---------\n\n\n----------------------------------------------------------------")
                    print("Created Partner on remote: %s (ID: %s)", partner.name, remote_partner_id)
                    print(
                        "DSDSDSDSD-------------------------\n\n\n----------------------------------------------------------------")

                    last_processed_id = partner.id
                    partner.write({'posted_to_remote': True, 'last_processed_partner_id': partner.id})

                    # Update the last processed ID in ir.config_parameter
                _logger.info(f"Batch processing completed. Last processed partner IDn\n\n\n\n")
                _logger.info("All partners (Companies) processed successfully.")
                print(
                    "DONE-------------------------\n\n\n----------------------------------------------------------------")
            else:
                self._process_customers_individual(db=db, uid=uid, password=password, partners=partners,
                                                   )

        except Exception as e:
            _logger.error(f"An error occurred in action_send_partner: {str(e)}")

    def _get_remote_id(self, models, db, uid, password, model, field, value):
        """
        Fetch the remote ID for a given model and field.
        """
        if not value:
            return False

        remote_id = models.execute_kw(
            db, uid, password, model, 'search', [[(field, '=', value)]], {'limit': 1}
        )
        return remote_id[0] if remote_id else False

    def _process_customers_individual(self, db, uid, password, partners):
        for rec in self:
            # Individuals Partners
            individuals = partners.filtered(lambda p: not p.is_company)
            individuals_filtered = individuals

            if len(individuals_filtered) == 0:
                """ Stop processing"""
                return

            for partner in individuals_filtered:
                # Replace this with the actual logic for sending partner data to the remote system
                _logger.info(f"Processing partner ID: {partner.id}, Name: {partner.name}")

                # Check if partner exists on remote
                existing_partner_ids = models.execute_kw(
                    db, uid, password, 'res.partner', 'search',
                    [[('name', '=', partner.name), ('email', '=', partner.email)]]
                )

                if existing_partner_ids:
                    _logger.info("Partner already exists on remote: %s", partner.name)
                    continue

                # Prepare partner data for remote creation
                partner_data = {
                    'name': partner.name,
                    'email': partner.email,
                    'is_company': partner.is_company,
                    'phone': partner.phone,
                    'mobile': partner.mobile,
                    'street': partner.street,
                    'street2': partner.street2,
                    'related_company': partner.related_company if partner.related_company else False,
                    'city': partner.city,
                    'state_id': self._get_remote_id(models, db, uid, password, 'res.country.state', 'name',
                                                    partner.state_id.name) if partner.state_id else False,
                    'country_id': self._get_remote_id(models, db, uid, password, 'res.country', 'name',
                                                      partner.country_id.name) if partner.country_id else False,
                    'company_id': self._get_remote_id(models, db, uid, password, 'res.company', 'name',
                                                      partner.company_id.name) if partner.company_id else False,

                    # Accounts
                    'property_account_receivable_id': self._get_remote_id(models, db, uid, password, 'account.account', \
                                                                          partner.property_account_receivable_id.code),
                    'property_account_payable_id': self._get_remote_id(models, db, uid, password, 'account.account', \
                                                                       partner.property_account_payable_id.code),
                }

                # Create partner on remote
                remote_partner_id = models.execute_kw(db, uid, password, 'res.partner', 'create', [partner_data])
                _logger.info("Created Partner on remote: %s (ID: %s)", partner.name, remote_partner_id)
                print("DSDSDSDSD---------\n\n\n----------------------------------------------------------------")
                print("Created Partner (INDIVIDUALS) on remote: %s (ID: %s)", partner.name, remote_partner_id)
                print(
                    "DONE (IND)------------------------\n\n\n (INDIVIDUALS)-----------------------------------------------------------")

                # Mark the partner as posted to remote
                partner.write({'posted_to_remote': True})
            _logger.info("All partners processed successfully.")


    # @api.model
    # def action_send_partner(self):
    #     # Retrieve remote server configurations
    #     config = self.env['ir.config_parameter'].sudo()
    #     if config.get_param('remote_operations.remote_type') != 'Branch Database':
    #         _logger.info("Not a Branch Database. Skipping.")
    #         return
    #
    #     url = config.get_param('remote_operations.url')
    #     db = config.get_param('remote_operations.db')
    #     username = config.get_param('remote_operations.username')
    #     password = config.get_param('remote_operations.password')
    #
    #     if not all([url, db, username, password]):
    #         raise ValidationError("Remote server settings are incomplete.")
    #
    #     # Authenticate with the remote server
    #     common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
    #     uid = common.authenticate(db, username, password, {})
    #     if not uid:
    #         raise ValidationError("Authentication failed.")
    #
    #     models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
    #
    #     # Retrieve last processed ID to avoid duplications
    #     last_processed_id = int(config.get_param('res_partner_last_processed_id', default=0))
    #
    #     # Process partners in batches
    #     partners = self._get_partner_batch(last_processed_id)
    #     while partners:
    #         self._process_partner_batch(partners, models, db, uid, password)
    #
    #         # Update last processed ID and mark partners as processed
    #         last_processed_id = partners[-1].id
    #         partners.write({'posted_to_remote': True})
    #         config.set_param('res_partner_last_processed_id', last_processed_id)
    #
    #         # Fetch next batch
    #         partners = self._get_partner_batch(last_processed_id)
    #
    #     _logger.info("Partner data sync complete.")
    #
    # def _get_partner_batch(self, last_processed_id):
    #     """Fetch a batch of partners to process, prioritizing companies."""
    #     return self.search([
    #         ('active', '=', True),
    #         ('posted_to_remote', '=', False),
    #         ('id', '>', last_processed_id)
    #     ], order='is_company desc, id asc', limit=BATCH_SIZE)
    #
    # def _process_partner_batch(self, partners, models, db, uid, password):
    #     """Process a batch of partners."""
    #     for partner in partners:
    #         try:
    #             partner_data = {
    #                 'name': partner.name,
    #                 'email': partner.email,
    #                 'phone': partner.phone,
    #                 'mobile': partner.mobile,
    #                 'street': partner.street,
    #                 'city': partner.city,
    #                 'state_id': self._get_remote_id(models, db, uid, password, 'res.country.state', 'name',
    #                                                 partner.state_id.name) if partner.state_id else False,
    #                 'country_id': self._get_remote_id(models, db, uid, password, 'res.country', 'name',
    #                                                   partner.country_id.name) if partner.country_id else False,
    #                 'is_company': partner.is_company,
    #                 'parent_id': self._get_remote_id(models, db, uid, password, 'res.partner', 'name',
    #                                                  partner.parent_id.name) if partner.parent_id else False,
    #             }
    #
    #             # Send data to remote server
    #             remote_partner_id = models.execute_kw(db, uid, password, 'res.partner', 'create', [partner_data])
    #             _logger.info("Partner %s synced with remote ID %s", partner.name, remote_partner_id)
    #
    #         except Exception as e:
    #             _logger.error("Failed to sync partner %s: %s", partner.name, str(e))
    #
    # def _get_remote_id(self, models, db, uid, password, model, field, value):
    #     """Fetch or create a remote ID based on a model, field, and value."""
    #     try:
    #         remote_ids = models.execute_kw(db, uid, password, model, 'search', [[(field, '=', value)]], {'limit': 1})
    #         if remote_ids:
    #             return remote_ids[0]
    #
    #         # If not found, create it remotely
    #         return models.execute_kw(db, uid, password, model, 'create', [{field: value}])
    #
    #     except Exception as e:
    #         _logger.error("Failed to get remote ID for model %s, field %s, value %s: %s", model, field, value, str(e))
    #         return False

