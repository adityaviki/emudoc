# The account root against a mocked AWS (tofu test, in this root's directory): who may push an
# image, and with what. Nothing reaches AWS.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:eu-central-1:123456789012:repository/emudoc"
      repository_url = "123456789012.dkr.ecr.eu-central-1.amazonaws.com/emudoc"
    }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/emudoc-github-push" }
  }
}

run "ci_alone" {
  assert {
    condition = jsondecode(aws_iam_role.push.assume_role_policy).Statement[0] == {
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:emudocai/emudoc:ref:refs/heads/main"
        }
      }
    }
    error_message = "Only a job of a workflow on the repository's main takes on the push role."
  }
  assert {
    condition = (
      aws_iam_openid_connect_provider.github.url == "https://token.actions.githubusercontent.com"
      && aws_iam_openid_connect_provider.github.client_id_list == toset(["sts.amazonaws.com"])
    )
    error_message = "AWS takes GitHub's tokens meant for STS."
  }
  assert {
    condition = one([
      for statement in local.push_policy.Statement : statement.Resource if statement.Sid == "Push"
    ]) == "arn:aws:ecr:eu-central-1:123456789012:repository/emudoc"
    error_message = "CI pushes to this repository alone."
  }
  assert {
    condition = jsondecode(aws_ecr_repository_policy.app.policy).Statement == [{
      Sid       = "PushedByCIAlone"
      Effect    = "Deny"
      Principal = "*"
      Action    = ["ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"]
      Resource  = "arn:aws:ecr:eu-central-1:123456789012:repository/emudoc"
      Condition = { ArnNotEquals = { "aws:PrincipalArn" = ["arn:aws:iam::123456789012:role/emudoc-github-push"] } }
    }]
    error_message = "The repository refuses an image from anyone but CI's role."
  }
  assert {
    condition     = aws_ecr_repository.app.image_tag_mutability == "IMMUTABLE"
    error_message = "A tag is one build for good."
  }
  assert {
    condition     = output.push_role == "arn:aws:iam::123456789012:role/emudoc-github-push"
    error_message = "The role is an output, for the repository variable."
  }
}

run "an_operator_while_github_is_down" {
  command = plan
  variables {
    pushers = ["arn:aws:iam::123456789012:role/operator"]
  }
  assert {
    condition = local.repository_policy.Statement[0].Condition.ArnNotEquals["aws:PrincipalArn"] == [
      "arn:aws:iam::123456789012:role/emudoc-github-push", "arn:aws:iam::123456789012:role/operator",
    ]
    error_message = "pushers lets another role push beside CI."
  }
}
