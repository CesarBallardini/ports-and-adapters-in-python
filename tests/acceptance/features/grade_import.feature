Feature: A teacher imports a term's grades from a spreadsheet
  UC-40. The scenario this repository is built to make checkable.

  Every scenario below runs twice, once against the CSV adapter and once against the XLSX one,
  and demands the same outcome from both. That is not thoroughness — it is the claim. The rules
  live above the spreadsheet port, so the choice of format cannot reach them; if one ever did,
  these scenarios would disagree with themselves and say so.

  Background:
    Given Grace teaches Mathematics this term
    And Ada and Bob are enrolled in it
    And Zoe is a student who is not

  Scenario Outline: Every valid row is recorded
    Given a <format> grade sheet with
      | student_email     | grade |
      | ada@academy.test  | 8     |
      | bob@academy.test  | 4     |
    When Grace imports it
    Then 2 rows are recorded
    And no row is rejected
    And Ada's best grade in Mathematics is 8

    Examples:
      | format |
      | csv    |
      | xlsx   |

  Scenario Outline: One bad row costs only itself
    Given a <format> grade sheet with
      | student_email       | grade |
      | ada@academy.test    | 8     |
      | nobody@academy.test | 7     |
      | bob@academy.test    | 4     |
    When Grace imports it
    Then 2 rows are recorded
    And row 3 is rejected because no student has that email
    And Ada's best grade in Mathematics is 8

    Examples:
      | format |
      | csv    |
      | xlsx   |

  Scenario Outline: A student who is not in the section is rejected
    Given a <format> grade sheet with
      | student_email    | grade |
      | zoe@academy.test | 8     |
    When Grace imports it
    Then 0 rows are recorded
    And row 2 is rejected because the student is not enrolled

    Examples:
      | format |
      | csv    |
      | xlsx   |

  Scenario Outline: A grade the domain refuses is rejected, and the file is not
    Given a <format> grade sheet with
      | student_email    | grade  |
      | ada@academy.test | eleven |
      | bob@academy.test | 4      |
    When Grace imports it
    Then 1 rows are recorded
    And row 2 is rejected because the grade is not a grade

    Examples:
      | format |
      | csv    |
      | xlsx   |

  Scenario Outline: A dry run says what would happen and changes nothing
    Given a <format> grade sheet with
      | student_email       | grade |
      | ada@academy.test    | 8     |
      | nobody@academy.test | 7     |
    When Grace tries it out without saving
    Then 1 rows are recorded
    And row 3 is rejected because no student has that email
    And Ada has no grades at all

    Examples:
      | format |
      | csv    |
      | xlsx   |

  Scenario Outline: The headers are read the way a person writes them
    Given a <format> grade sheet with
      | Student Email    | GRADE |
      | ada@academy.test | 8     |
    When Grace imports it
    Then 1 rows are recorded
    And no row is rejected

    Examples:
      | format |
      | csv    |
      | xlsx   |

  Scenario Outline: Somebody else's section is not importable
    Given a <format> grade sheet with
      | student_email    | grade |
      | ada@academy.test | 8     |
    When Nemo imports it
    Then the import is refused
    And Ada has no grades at all

    Examples:
      | format |
      | csv    |
      | xlsx   |

  Scenario: A file that is not a spreadsheet is refused as one error, not as rejected rows
    Given a file that is not a spreadsheet at all
    When Grace imports it
    Then the file is refused as unreadable
