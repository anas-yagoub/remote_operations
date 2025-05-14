# -*- coding: utf-8 -*-
{
    'name': "Remote Operations",

    'summary': """
        Remote Operations
        """,

    'description': """
        Remote Operations
    """,

    'author': "Tech Things",
    'website': "https://www.techthings.it",

    'category': 'Uncategorized',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base', 'account','stock_account'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/views.xml',
        'views/invoice_bill_view.xml',
        'views/account_payment.xml',
        'views/menus.xml',
        'views/journal_entry.xml',
        'views/bank_statement_line.xml',
    ],
}
